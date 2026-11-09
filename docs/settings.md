# Settings and sensors

[Українською](settings.uk.md) · [README](../README.md) · [Home Assistant automations](automations.md)

Settings live in three places:

1. **Controls in Home Assistant**: change them any time, they survive restarts.
2. **Firmware settings** (`substitutions:`): set in the device file, applied by the next flash.
3. **Adapter capabilities**: in the adapter file. Change them only when writing an adapter.

Entity ids below assume `device_name: blackout-inverter`, so `select.blackout_inverter_energy_strategy`
and so on.

## Controls in Home Assistant

### Everyday

| Entity | Default | What it does |
|---|---|---|
| `Energy Strategy` (select) | Reserve | **Sun**: 3 h reserve, for days the sun covers. **Reserve**: 6 h, the everyday mode under scheduled outages. **Backup**: the battery is held at 90%, the grid carries the house; for unscheduled outages. Applies at once. |
| `Battery Capacity` (Ah) | 200 | The pack's capacity. The reserve, the autonomy estimate and the Home Assistant plan are computed from it. **Set it first.** |
| `Target Charge Current` (A) | 20 | The top of a ramped grid charge (night, after an outage, outage plan, Backup, Manual Charge). 10–40 A. Raised during a charge: ramps up to it. Lowered: applies at once. |
| `Manual Charge` (switch) | off | Charge to 100% now, at `Target Charge Current`, with the same careful start. While it is on, no automatic charge starts. |
| `Manual Charge Auto-Disable` (switch) | off | Lets the firmware end a manual charge: at a full pack, or when the grid returns from an outage longer than 30 min. A shorter loss is taken for the house relay tripping, and the charge goes on. Automatic charges may also take over a manual one. |

### Written by Home Assistant automations

Each expires 12 h after the last write, so a dead Home Assistant cannot leave it stuck.

| Entity | Default | Written by | What it does |
|---|---|---|---|
| `Reserve Autonomy Target` (h) | 0 | outage schedule | Hours the reserve covers, overriding the strategy. 0 = follow the strategy. Raising it is the way to make the device charge earlier: SoC falls under the hold band and the daytime rebuild starts. |
| `Night Charge Bump` (%) | 0 | Battery Smart Management | Added to tonight's target: the part of tomorrow's load the sun will not cover. Set at 22:00, cleared at 07:05. 0–40. |
| `Outage Plan SoC` (%) | 0 | Outage Charge Plan | SoC needed for the outage blocks ahead. Raises the night target (up to 100%); by day starts a top-up to it (up to 90%). 0 = no plan. |

### Rarely

| Entity | Default | What it does |
|---|---|---|
| `Automation: Night Charge` | on | The night-window charge, and holding the night target overnight. Off: no night charge; the grid charges only under the hold band, after an outage, below 15% and on Manual Charge. For a single-rate meter, if you prefer the sun to do the refilling. |
| `Automation: Emergency Charge` | on | The daytime rebuild when SoC falls under the hold band. |
| `Automation: Battery Protection` | on | Stops automatic charges at 99%; charges at 40 A below 15% when the grid is there. |
| `Full Manual Mode` | off | The firmware leaves the inverter alone: it puts it in a clean idle state once, then stops writing. For working on the inverter by hand. |
| `Web UI & Fallback AP` | on | The ESP's web page and its own WiFi hotspot when the network is gone. A change restarts the ESP. |
| `Nightly Restart` | on | Restarts the ESP at 03:00, skipped while charging or if it restarted in the last 6 h. |
| `Must Inverter Restart` | — | Restarts the ESP (not the inverter; the name is historical). |

The inverter's own settings (`Energy use mode`, `Charger source priority`, `Max Grid Charger
current`, `Battery stop charging voltage`, ...) also appear in Home Assistant. The firmware
controls them and puts back any manual change within a minute or two. To set them by hand, turn on
`Full Manual Mode` first.

## Sensors worth watching

| Sensor | Meaning |
|---|---|
| `[EH] Charge Status` | One line: what is happening (below), current, strategy, reserve, ⚡ = outage. |
| `[EH] Reserve Target SoC` | Reserve for an outage starting now. Drives the day. |
| `[EH] Reserve Peak SoC` | Reserve for the worst window of the coming day. Drives the night target. |
| `[EH] Active Charge Limit (%)` | Where the current or next charge stops. |
| `[EH] Active Discharge Limit (%)` | Below this the battery is no longer used freely. |
| `[EH] Battery Backup Autonomy` | Hours the battery alone would carry the average load. |
| `[EH] Battery Charge Time Estimate` | Time to the charge target at the current rate. |
| `[EH] Grid Present` | Grid voltage on the inverter's input (150 V or more). |
| `[EH] Outage Time Today`, `[EH] Outage Count Today` | Outage log for the day. |
| `[EH] Charging Active`, `[EH] Active Charge Amps` | Grid charge session and its current. |
| `[EH] Load Power`, `[EH] Average Load Power`, `[EH] Daily Average Load` | House load: now, ~1 h average, ~day average. |
| `[EH] Expected Daily Load`, `[EH] Expected Day Load` | kWh for the next 24 h and for the hours outside the night window, from the learned profile. |
| `[EH] Hourly Load Profile` | Learned load by hour of day (disabled by default). |
| `[EH] Daily ... Energy` | Solar, load, grid purchase, battery in and out since midnight. Reset by an ESP restart. |
| `ESP Status`, `ESP Uptime`, `ESP Reset Reason`, `ESP Last Self-Restart`, `ESP Heap ...` | ESP health. |

`[EH] Charge Status` values:

| Status | Meaning |
|---|---|
| 💤 Standby | no grid charge |
| 🌙 Night | night-window charge |
| ⚠️ Rebuild | daytime rebuild of a breached reserve |
| 📋 Plan | top-up for the outage plan |
| 🛡️ Top-up | Backup strategy charge |
| ⛔ Critical | charge below 15% |
| 🔧 Manual | `Manual Charge` |
| 🔌 Charging | a charge before the clock is known |
| 🔒 MANUAL | `Full Manual Mode` |

Then ` 20A` or ` ⬆️5→20A` (ramping), the strategy (☀️ Sun, 🔋 Reserve, 🛡️ Backup), ` R45%`
(reserve), and ` ⚡` during an outage.

## Firmware settings

All are in [core/defaults.yaml](../core/defaults.yaml), each with a comment on where its value
came from. Override one under `substitutions:` in the device file:

```yaml
substitutions:
  device_name: blackout-inverter
  battery_cells_s: "16"
  night_start_hour: "23"
```

Values are quoted strings. The defaults are the reference install's: MUST PV18, 16S LiFePO4
190 Ah, a weak line.

### Battery

| Setting | Default | Meaning |
|---|---|---|
| `battery_cells_s` | 16 | LiFePO4 cells in series (8 = 24 V, 16 = 48 V, 24 = 72 V). All voltages follow from it. LiFePO4 only. |
| `batt_stop_dischg_v` | 49.0 | Inverter's discharge cut-off, written to the inverter at boot. |
| `critical_low_soc` | 15 | Bottom of the usable range; under it the grid charges at 40 A. Keep it at or above the SoC of `batt_stop_dischg_v`, so the reserve counts real energy. |
| `critical_high_soc` | 99 | Overcharge guard for automatic charges. |
| `critical_safe_soc` | 25 | Above this the critical alarm re-arms. |
| `inverter_efficiency` | 0.87 | Inverter efficiency, used for the reserve. Measured on the reference install. |
| `inverter_idle_w` | 27 | Inverter's own draw, W. |

### Reserve

| Setting | Default | Meaning |
|---|---|---|
| `reserve_hours_sun` | 3 | Hours of autonomy, Sun strategy. |
| `reserve_hours_reserve` | 6 | Hours of autonomy, Reserve strategy. The main setting. |
| `backup_target_soc` | 90 | SoC held by the Backup strategy. |
| `reserve_soc_min` / `reserve_soc_max` | 25 / 70 | The reserve is kept within these bounds. |
| `reserve_hold_band` | 10 | Width of the band under the reserve where the grid carries the house but does not charge. |
| `reserve_mode_deadband` | 2 | Leaving the band needs SoC this far above the reserve (no relay chatter). |
| `mode_switch_dwell_minutes` | 10 | Minimum time between battery ⇄ grid switches of the house. |
| `post_outage_min_deficit` | 5 | A top-up after an outage starts only this far under the reserve. |
| `reserve_override_ttl_hours` | 12 | How long a `Reserve Autonomy Target` from Home Assistant is trusted. |
| `ha_absent_worst_case_minutes` / `reserve_hours_worst_case` | 60 / 10 | Without Home Assistant this long, the Reserve strategy holds this many hours. |
| `min_valid_load_w` | 15 | Load readings below this are ignored as bad. |

### Night window

| Setting | Default | Meaning |
|---|---|---|
| `timezone` | Europe/Kyiv | Time zone for all hours below. |
| `night_start_hour` / `night_end_hour` | 23 / 7 | Night window: when the battery is refilled for the day. Put your night-rate hours here if you have them. |
| `reserve_night_margin` | 5 | Night target = peak reserve + this (+ bump, + plan). |
| `night_target_soc_max` | 90 | Ceiling of an automatic charge (only the outage plan may go to 100%). |
| `reserve_restart_hysteresis` | 5 | A night charge (re)starts this far under the target. |
| `morning_topup_hour` / `reserve_restart_hysteresis_morning` | 5 / 2 | From this hour on, it restarts already 2% under the target, so the day starts full. |
| `morning_stop_hour` / `morning_stop_minute` | 7 / 2 | A night charge ends here; the sun takes over. |
| `manual_target_soc` | 100 | Where `Manual Charge` stops. |
| `nightly_restart_hour` | 3 | Hour of the `Nightly Restart`. |

### Charge current

| Setting | Default | Meaning |
|---|---|---|
| `charge_amps_start` | 5 | First step of every grid charge. Also the lowest current ever written: some inverters store 0 A as their maximum. |
| `charge_amps_step` | 5 | Ramp step, up and down. |
| `charge_ramp_step_s` | 60 | At most one step up per this many seconds. |
| `charge_amps_min` / `charge_amps_max` | 10 / 40 | Bounds of `Target Charge Current`. |
| `charge_amps_default` | 20 | Used when `Target Charge Current` is unset. |
| `charge_amps_slow` | 15 | Daytime rebuild of a breached reserve. |
| `charge_amps_critical` | 40 | Charge below `critical_low_soc`. |
| `charge_amps_idle` | 10 | Limit left on the inverter between sessions (Backup charges at it). |

### Grid and a weak line

| Setting | Default | Meaning |
|---|---|---|
| `grid_present_min_voltage` | 150 | Under this the grid counts as gone. |
| `grid_settle_minutes` / `grid_settle_max_minutes` | 2 / 10 | Stage 0: the house stays on the battery this long after a return. Doubles per repeated loss. |
| `long_outage_hours` / `grid_settle_long_minutes` | 3 / 10 | After an outage this long, stage 0 lasts at least 10 min. |
| `grid_stable_voltage` / `grid_stable_minutes` / `grid_stable_max_minutes` | 205 / 3 / 10 | Stage 2: the voltage must hold this high this long before 5 A. Doubles per repeated loss. |
| `grid_falling_volts` / `grid_falling_window_minutes` | 4 / 5 | ...and must not be more than 4 V under the 5-min maximum. |
| `grid_voltage_resume` | 215 | Ramp up only at or above this. |
| `grid_voltage_min` | 207 | Under this the current steps down. |
| `grid_charge_pause_voltage` | 200 | Under this charging pauses, for `weak_grid_min_minutes` at least. |
| `weak_grid_voltage` | 195 | Under this the house goes on the battery too. |
| `weak_grid_soc_margin` | 10 | ...if SoC is this far above `critical_low_soc` (the same floor guards stage 0). |
| `weak_grid_min_minutes` / `weak_grid_max_minutes` | 10 / 20 | How long the house stays on the battery after a weak line, and the shortest charge pause under 200 V. Doubles per repeat. |
| `weak_grid_clear_margin` | 3 | The house goes back once the line holds this far above `weak_grid_voltage`. |
| `relay_trip_return_minutes` | 30 | A mid-charge loss back within this counts as the house relay tripping... |
| `relay_trip_backoff_amps` / `relay_cap_minutes` | 10 / 30 | ...and caps the current at (current at the loss − 10 A) for 30 min. |
| `relay_trip_window_s` / `relay_trip_settle_minutes` | 180 / 5 | A loss within 3 min of a step up: stage 0 lasts at least 5 min. |

Repeated means within an hour of the previous loss. After an hour without one the waits reset.

### Self-heal

| Setting | Default | Meaning |
|---|---|---|
| `heal_api_dead_minutes` | 10 | Restart with WiFi up but no Home Assistant. |
| `heal_wifi_dead_minutes` | 30 | Restart without WiFi while the grid is up. |
| `heal_wifi_dead_outage_minutes` | 30 | The same during an outage. |
| `heal_modbus_dead_minutes` | 20 | Restart with no data from the inverter. |
| `heal_max_streak` / `heal_backoff_minutes` | 2 / 180 | After 2 restarts that did not help, at most one per 3 h. |
| `updates` | 30s | Inverter poll interval. |

### Board

| Setting | Default (`boards/d1-mini.yaml`) | Meaning |
|---|---|---|
| `tx_pin` / `rx_pin` | GPIO13 / GPIO12 | UART to the RS485/RS232 module. |
| `flow_control_pin` | GPIO14 | DE+RE of a MAX485. |

## Adapter capabilities

Set in the adapter file, where it includes `common/voltronic-sync.yaml`. With the remote form of
the device file, change them in a local copy of the repository.

| Variable | Meaning |
|---|---|
| `writes_enabled` | `"0"`: read only, nothing is written to the inverter. New adapters ship with `"0"`. |
| `cap_solar_aim` | The inverter has a solar priority setting (load first / battery first). |
| `cap_grid_only` | The inverter grid-charges by day only in a grid-first mode. |
| `stop_by_soc` | Charge stop and discharge stop are set in % SoC, not in volts. |
