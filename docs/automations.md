# Home Assistant automations

[Українською](automations.uk.md) · [README](../README.md) · [Settings and sensors](settings.md)

The device charges on its own. Home Assistant adds what the ESP cannot see: the outage
schedule, the solar forecast and the load history. It sends them as three numbers on the
device: `Reserve Autonomy Target`, `Night Charge Bump` and `Outage Plan SoC`. Each expires 12 h
after the last write, so when Home Assistant stops, the device falls back to its own logic.

Every file is optional. Take what fits:

| You have | Take |
|---|---|
| nothing but the device | `esp-offline.yaml`, `flash-guard.yaml` (+ the `input_text` from `helpers.yaml`) |
| a PV forecast | + `helpers.yaml`, `automation.yaml` |
| an outage schedule | + `outage-schedule-automation.yaml` |
| both | + `outage-plan.yaml` and `custom_templates/outage_plan.jinja` |

## Install

1. **Package.** Copy [automation/helpers.yaml](../automation/helpers.yaml) to `/config/` and add
   it to `configuration.yaml`:
   ```yaml
   homeassistant:
     packages:
       blackout: !include helpers.yaml
   ```
2. **Template macro** (for the outage plan). Copy
   [automation/custom_templates/outage_plan.jinja](../automation/custom_templates/outage_plan.jinja)
   to `/config/custom_templates/`.
3. **Automations.** For each file: Settings → Automations → Create → ⋮ → Edit in YAML, paste,
   save.
4. **Restart Home Assistant** (the package needs it).

Before pasting, replace the names that belong to the reference install:

| In the files | Replace with |
|---|---|
| `blackout_inverter_` | your `device_name` with `_` instead of `-` |
| `svitlo_region_queue` | your outage integration's entities, e.g. `svitlo_kyivska_oblast_1_1` (the `svitlo_live` integration; any integration with an outage calendar works) |
| `notify.notify` | your phone, `notify.mobile_app_...` |
| `* 51.2` (`helpers.yaml`, `automation.yaml`) | your pack's nominal voltage: cells × 3.2 |
| forecast entities in `helpers.yaml` | your forecast integration's (below) |

## helpers.yaml: forecast, plan, helpers

A package. Creates:

| Entity | What it is |
|---|---|
| `sensor.pv_remaining_today_total` | PV still to come today, kWh, calibrated. |
| `sensor.pv_tomorrow_raw` | Tomorrow's forecast, raw: the lower of two integrations. |
| `sensor.pv_tomorrow_total` | Tomorrow's forecast × learned factor. |
| `sensor.outage_charge_plan` | SoC needed for the outage blocks ahead. Recomputed every 30 min; fire the `outage_plan_refresh` event to recompute now. |
| `sensor.battery_soc_max_today` | Highest SoC of the last 12 h. Days the pack filled are not used to calibrate the forecast. |
| `input_number.pv_forecast_factor` | Forecast correction learned from real harvest, 0.3–1.3. |
| `input_number.pv_forecast_made`, `input_text.pv_forecast_made_date` | Last night's forecast, kept for the calibration. |
| `input_text.esp_settings_snapshot` | For `flash-guard.yaml`. |

The forecast uses **Forecast.Solar** (`sensor.energy_production_*`, `_2` for the second roof plane)
and **Open-Meteo Solar Forecast** (`sensor.panels_1_*`, `panels_2_*`). The hourly shape for the
plan comes from Open-Meteo only. With one integration the other falls back; with one plane, drop
the `_2` / `panels_2` terms; with three, add them the same way. Without an outage calendar
`sensor.outage_charge_plan` stays unknown, and nothing is sent.

## automation.yaml: Battery Smart Management

Sizes tonight's charge from tomorrow's forecast and relays the device's notifications.

- **22:00.** Calibrates the forecast factor from today's harvest, then computes tomorrow's
  shortfall: the load outside the night window (from the device's learned profile) minus the forecast PV.
  Writes it to `Night Charge Bump` (% of the pack, at most 40) and sends a summary to the phone.
  The night charge then buys tomorrow's missing energy overnight (at the night rate, if any).
- **07:05.** Sets `Night Charge Bump` to 0.
- **After a restart** between 22:00 and 06:30 it recomputes the bump.
- **`esphome.battery_notification` events** from the device (charge started or finished, critical,
  outage, ...) go to `notify.notify`.

Edit at the `USER CONFIGURABLE` block: `night_margin_soc` (= `reserve_night_margin`),
`inverter_efficiency`, `inverter_idle_w`, `day_hours` (hours outside the night window).

Needs: `helpers.yaml`, a PV forecast, `Automation: Night Charge` on. Without a forecast the bump is the maximum and a persistent
notification says so.

## outage-schedule-automation.yaml: reserve from the schedule

Sets `Reserve Autonomy Target` to the longest outage block in the next 24 h, plus 30 min, between
2 and 10 h. Blocks less than 1 h apart count as one. Emergency outages flag on: 10 h. No blocks: 0,
so the strategy decides again. Runs on every schedule change and every 30 min (that keeps the
12 h expiry from running out).

When a long block is coming, the reserve rises, SoC falls under the hold band, and the device
charges now, at the day rate if it must. It never touches `Energy Strategy`.

Edit at the `USER CONFIGURABLE` block: `outage_calendar`, `emergency_sensor`, `emergency_hours`,
`min_hours`, `max_hours`, `margin_hours`, `merge_gap_hours`. Replace the entity ids in the
triggers too.

## outage-plan.yaml + outage_plan.jinja: outage charge plan

`sensor.outage_charge_plan` (in `helpers.yaml`, with the macro) walks back through the next 30 h:
outage blocks (+30 min each), hourly PV forecast and hourly load (14 days of history × 1.2). It
returns the SoC the battery needs so that it never falls under 25%. This automation sends
that number to `Outage Plan SoC` every 30 min and on every change. It never sends a plan older
than 75 min.

On the device:
- **Night:** the target goes up to the plan, past the usual 90% up to 100% if needed.
- **Day:** tops up to the plan (at most 90%) only for what the sun and the coming night cannot
  cover. While SoC is at or below the plan, the grid carries the house.
- **Emergency outages flag on:** 100%, take the grid whenever it is there.
- **12 h or more of outages in the next 24 h:** the night plan is 100%.

Needs: `helpers.yaml`, the macro, an outage calendar, `Automation: Night Charge` on (the plan
counts on the night charge); a PV forecast is optional (without one it
plans as if there were no sun). Design and reasoning:
[design/outage-charge-plan.md](design/outage-charge-plan.md). Test with
`python tools/outage_plan_test.py`.

## esp-offline.yaml: ESP offline alert

A phone notification when the device has been gone from Home Assistant for 10 min, and another
when it is back. An ESP that only lost WiFi still charges. One that hung or lost power does not,
so this tells you to power-cycle it.

## flash-guard.yaml: lost settings alert

Every 10 min, with the ESP up for 10+ min, it saves its restored settings (capacity, current,
strategy, switches) to `input_text.esp_settings_snapshot`. After a restart it compares: if two
or more came back different, a worn ESP8266 flash sector most likely reset them, and you get a
notification listing them. One setting changed by you never trips it.

## strategy-reminder.yaml: Sun ⇄ Reserve hint

Sundays at 20:00: suggests **Sun** once the panels covered the whole day's load 7 days running,
and back to **Reserve** once they fell short (under 80% of the load) on 3 of the last 7 days.
Notification only. It reads `sensor.blackout_inverter_pv1_power`; with two MPPTs or another
adapter, point it at your total PV power.

## Dashboard card

[information-card.yaml](../information-card.yaml): grid, autonomy, reserve and target chips, the
status line, strategy and charge controls. Needs the
[mushroom](https://github.com/piitaya/lovelace-mushroom) cards (HACS). Dashboard → Edit → Add card
→ Manual, paste.
