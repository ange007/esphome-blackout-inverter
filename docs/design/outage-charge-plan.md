# Outage charge plan — design

Date: 2026-11-03. Status: implemented 2026-11-03.

## Problem

The reserve is sized for the longest single outage block and capped at `reserve_soc_max` (70%).
On a day like 3 Nov (5.2: 06:00-10:00, 13:00-20:30, 23:30-00:00; ~12 h without power) the
evening block starts at ≤70% and ends near 25-30%. Nothing looks at the day as a whole: how
much each grid window can actually put back, how little sun there is, and whether the night
(cheap tariff) could carry more so the day does not have to.

## Goal

Before every outage day, charge so that the lowest point of the next ~30 h stays at or above
**25% SoC**, preferring the night tariff, and buying day-rate grid only for what the night
cannot cover. Days without scheduled outages, sunny days and low-consumption days must behave
exactly as today (no extra grid purchases).

## Measured facts (21 days of HA history, 17 scheduled blocks, queue 5.2)

| | median | p80 | max |
|---|---|---|---|
| power cut vs schedule start | 9 min late | 25 min late | 73 min late |
| power back vs schedule end | 3 min early | 7 min late | 19 min late |
| actual / scheduled length | 0.92 | 0.96 | 1.22 (short block) |

So the schedule is, if anything, pessimistic. The margin is a fixed **+30 min on each block's
end**, not a percentage. 15 grid losses in the period were unscheduled (emergency outages and
short flaps).

Hourly load, last 7 days (mean W): night 180-320, 05-07 ≈ 580, 09-12 ≈ 170-290, evening
480-670. PV on 3 Nov: 1.1 kWh for the whole day (forecast × learned factor 0.66).

Replaying 3 Nov with these numbers (night first, no day grid):

| scenario | needed at 06:00 | lowest point of the day |
|---|---|---|
| schedule, mean load | 90% | 25% |
| schedule, p80 load | 100% | 21% |
| the 10:30-13:00 window lost, p80 load | 100% | 17% |
| the window lost, mean load × 1.2 | 100% | 18% |

Mean load understates a heavy day by ~10 points; the plan uses **load × 1.2** (≈ p80). On a day
like this 25% cannot be guaranteed at all: the plan then does the most it can (100% at night,
every grid hour by day).

## Decisions

- Floor at the worst point: **25%** (critical is 15%).
- Night charge may go to **100%** (57.6 V cut-off) only when the plan needs it; otherwise the
  night cap stays 90%.
- Between blocks, top up to **what the next block needs** (day rate if necessary), capped 90%.
- **Night first:** day-rate grid is used only for what the night, charged up to 100%, cannot cover.
- **Emergency flag on** (`binary_sensor.svitlo_region_queue_emergency_outages`): power can
  go at any moment until the flag is cleared, so the plan is 100% outright: the ESP charges to
  100% at night and tops up to 90% whenever the grid is there by day (the computed need, with no
  future grid window trusted, stays in the attributes).
- Load for planning = hourly mean of the last 14 days from HA's long-term statistics
  (`recorder.get_statistics` on `[EH] Load Power`) **× 1.2**; the ESP's learned profile and its
  daily average are fallbacks. (Added after a flash wiped the ESP profile on 3 Nov.)
- Approach A: **HA computes, the ESP executes.** HA has the calendar and the hourly PV forecast;
  the ESP stays simple and keeps working without HA.

## Architecture

```
calendar 5.2 ─┐
PV wh_period ─┤                                    ┌─> number.blackout_inverter_outage_plan_soc (ESP)
load profile ─┼─> template sensor "Outage charge ─>│      night target = max(normal, plan), cap 100 if plan > 90
SoC, capacity,│    plan" (pure computation)        │      day top-up   = to min(plan, 90) when SoC < plan - 5
charge amps  ─┘          │                          └── automation: push every 30 min, on change, on HA start
                         └─ attributes: need now, need at next outage, next outage, day grid needed, feasible
```

### HA

- **Template sensor** (in `automation/helpers.yaml`, package `charge_helpers`). State =
  `outage_plan_soc`: the target for the current grid window (at night: the SoC to have when the
  grid goes; by day: the SoC needed now). 0 when no scheduled block is ahead. Attributes:
  `need_now`, `need_at_outage`, `next_outage`, `day_grid` (day-rate grid needed), `feasible`
  (25% holds even with a 100% start). The planner itself is a Jinja macro,
  `custom_templates/outage_plan.jinja`, so it can be tested through `/api/template`.
- **Automation** `automation/outage-plan.yaml`: writes the sensor value to the ESP number every
  30 min, when the sensor changes, and on HA start. No other logic.
- Inputs: `calendar.svitlo_region_queue_outages_schedule` (30 h of events),
  `sensor.panels_1_energy_production_{today,tomorrow}` + `panels_2_*` attribute `wh_period`
  (hourly Wh, both planes summed) × `input_number.pv_forecast_factor`, the ESP's hourly load
  profile, `sensor.blackout_inverter_battery_state_of_charge`, `number.blackout_inverter_battery_capacity`,
  `number.blackout_inverter_target_charge_current`.

### ESP

- New `number` **Outage Plan SoC** (0-100 %, restored, valid 12 h after the last write — same
  pattern as `night_charge_bump` / `effective_night_bump`).
- The learned hourly load profile reaches HA through the existing text sensor
  `[EH] Hourly Load Profile` (24 comma-separated watts), enabled in the entity registry.
- `night_target_soc()`: `max(current target, plan)`; cap 100 if the plan is above
  `night_target_soc_max` (90), else 90 as today.
- Day top-up: grid present, not night, plan valid and > 0, target = `min(plan, 90)`,
  `SoC < target - post_outage_min_deficit` → `start_charge_with_target(Target Charge Current, target)`. Same ramp and grid-sag
  regulation as every session.
- Plan hold: by day, with a valid plan, the grid carries the house (SUB + BLU + OSO) while
  SoC ≤ plan, released at plan + 2. The planner counts grid hours as grid-fed; in SBU the house
  would run off the pack, drain the night charge before the block and buy it back at the day
  rate, and a 100% emergency plan would cycle 90 → 85 → 90 every hour. (Added after the
  3 Nov review.)
- No valid plan → today's behaviour, unchanged. The plan only ever raises targets.

## Algorithm

Timeline from now to +30 h, cut into segments: **outage** (scheduled block, end +30 min,
overlapping/adjacent blocks merged) and **grid** (everything else). Hourly granularity.

Constants: pack Wh = capacity_Ah × 51.2; discharge efficiency 0.87 and inverter idle 27 W (as in
`ac_to_dc_wh`); charge power = Target Charge Current × 53 V × 0.7 (ramp, sags, late start).

Backward pass, `need` starting at the floor (25%) at the horizon:

- **outage hour:** `need += 100 × (max(0, load − pv)/0.87 + 27 Wh) / pack_Wh`;
  a PV surplus (`pv > load`) lowers `need` by `100 × (pv − load) × 0.95 / pack_Wh`.
- **future grid hour, night (23:00-07:00):** `need = max(floor, need − charge_pct_per_h − pv_surplus)`.
- **future grid hour, day:** pass 1 counts only PV surplus (no grid).
- **current grid segment:** counts PV, plus its night hours if the night is still ahead
  (so a 20:00 evening never buys day rate for a 06:00 block).
- `need` is capped at 100 at every step.

Pass 1 gives the night target. If it is ≤ 100, day windows need no grid. If it exceeds 100,
the night target is 100 and pass 2 lets day grid hours recover the difference, only as much as
needed. `outage_plan_soc` = `need` at "now" from the pass that applies.

Emergency flag on: every future grid hour counts as PV-only (pass 2 is not used) for the
attributes, and the plan itself is 100% (also with no scheduled block at all), so the ESP's
ordinary day top-up (to `min(plan, 90)`) takes the grid whenever it is there.

Worked example, 3 Nov (real hourly load × 1.2, PV 1.1 kWh, 9.7 kWh pack): the evening block
needs ~78% at 13:00, the morning block brings that to ≥ 100% at 06:00 → night target 100%;
day grid in 10:30-13:00 recovers what the cap cut off. An earlier rough estimate (64% at 06:00)
counted the day window at 30 A and mean load; it was too optimistic.

## Error handling

- Calendar call fails → `result` is empty, the sensor goes unknown, the push automation stops
  (it also refuses a plan computed more than 75 min ago), and the ESP drops the plan 12 h after
  the last write → today's logic. A missing PV forecast is not an error: it plans as if there
  were no sun, the conservative direction.
- Unlearned profile hours → the ESP's daily average (as the reserve already does).
- Emergency flag on → no future grid window is trusted (see Decisions); the existing 10 h
  reserve still applies, and the ESP takes the max of both.
- HA absent → the ESP's existing worst-case reserve (`ha_absent_worst`).

## Verification

Before deploying, render the template through HA's `/api/template` with substituted inputs:

1. no outages → 0;
2. sunny day, low load ("away from home") → plan below the normal targets, `day_grid` false;
3. the 3 Nov schedule → night target ≈ 100%, ~75% needed in the 10:30-13:00 window;
4. 14 h without power, a 2 h window, no sun → night 100% and a day top-up for the difference.

Firmware: `python tools/esphome_check.py compile`. After deployment, follow one real outage day
in the recorder (SoC at each block start and end vs the plan's attributes).

## Out of scope

Modelling emergency outages, tariffs other than night/day, per-appliance load forecasting.
