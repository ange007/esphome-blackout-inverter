# blackout-inverter

[Українською](README.uk.md)

ESPHome firmware for an ESP8266 wired to a hybrid inverter's RS485 port. It decides when the
home battery charges from the grid, for a house living with rolling blackouts:

- keeps enough charge for the next outage, sized from the load this house actually draws;
- lets the battery work the rest of the time, and refills it overnight (at the night rate, if you have one);
- charges whenever power comes back after an outage, at any hour;
- eases a weak line in gently, so the charger does not trip the house's voltage relay;
- runs on the ESP itself: Home Assistant adds the outage schedule and the solar forecast,
  but charging goes on without it.

The logic does not depend on the inverter model; a small adapter per model drives the inverter over Modbus.

> MUST register addresses follow MUST's own Modbus protocol (v1.4.15). Thanks to
> [vladyspavlov/esphome-must-inverter](https://github.com/vladyspavlov/esphome-must-inverter),
> the first MUST setup on ESPHome this project started from.

**Docs:** [settings and sensors](docs/settings.md) · [Home Assistant automations](docs/automations.md) ·
[adapter contract](docs/design/adapter-contract.md) · [outage plan](docs/design/outage-charge-plan.md) ·
[roadmap](docs/roadmap.md)

## Supported inverters

| Adapter | Inverter | Link | Status |
|---|---|---|---|
| `adapters/must-pv18.yaml` | MUST PV18 / PV19 (EP series) | RS485, Modbus RTU | **tested** on a live unit |
| `adapters/growatt-spf.yaml` | Growatt SPF off-grid | RS485, Modbus RTU | untested, writes off |
| `adapters/srne.yaml` | SRNE HF / ML | RS485, Modbus RTU | untested, writes off |
| `adapters/powmr-spf.yaml` | PowMr / Voltronic-clone SPF | RS232 (MAX3232), Modbus | untested, writes off |

An untested adapter reads everything and writes nothing (`writes_enabled: "0"`; the log says so
after boot). Compare every reading with the inverter's display, then set `writes_enabled: "1"` in
the adapter and watch the first charges. Each adapter's header lists its register sources and
doubts. Time-of-use hybrids (Deye/Sunsynk, Luxpower) and power stations are on the
[roadmap](docs/roadmap.md).

> **Safety.** This firmware switches a grid charger and the inverter's energy mode. A wrong
> register, pack voltage or cell count can overcharge a pack, trip a breaker or leave the house
> dark in an outage. Keep the inverter's and the BMS's own protections set correctly. No
> warranty, see [LICENSE](LICENSE).

## How it works

### The reserve

One number drives everything: the **reserve**, the SoC that still gives N hours of autonomy at
this house's load. It is recomputed every 30 s from a learned per-hour load profile, so it rises
before the evening peak and falls overnight.

- Above the reserve the battery carries the house; the grid is not used.
- In the 10% band under it (the **hold band**) the grid carries the house, the sun refills the
  battery, and there is no grid charging yet.
- Below the band the grid charges the battery back to the reserve at 15 A.
- At night the grid charges to a higher target for the day ahead (see below).

Two sensors show it: `[EH] Reserve Target SoC` (an outage starting now) drives the day;
`[EH] Reserve Peak SoC` (the worst N-hour window of the coming day) drives the night target.

### Energy Strategy

The `Energy Strategy` select sets how many hours the reserve covers:

| Strategy | Reserve | When | Grid |
|---|---|---|---|
| **Sun** | 3 h | the sun covers the day | almost never charges |
| **Reserve** (default) | 6 h, one outage block | little sun, scheduled outages | night + a slow rebuild |
| **Backup** | fixed 90% | unscheduled outages, shelling | carries the house, battery held full |

`Reserve Autonomy Target` (hours) overrides the strategy; the outage-schedule automation sets it
to the longest outage block ahead. The reserve is clamped to 25–70%.

### When the grid charges

| Trigger | Up to | Current |
|---|---|---|
| Night window 23:00–07:00, SoC under the night target | peak reserve + 5%, + forecast bump, at most 90% | ramped to `Target Charge Current` |
| Day, SoC under the hold band | reserve | 15 A |
| Grid back after an outage, SoC 5%+ under the reserve | reserve (by day) / night target (at night) | ramped |
| Outage plan from Home Assistant | the plan's SoC (by day at most 90%, at night up to 100%) | ramped |
| SoC under 15%, grid present | reserve / night target | 40 A |
| `Manual Charge` switch | 100% | `Target Charge Current` |
| Backup strategy | 90% | ramped |

"Ramped" means from 5 A up to `Target Charge Current`, as the line allows (next section). A
night charge ends at 07:02 even if not finished; the sun takes over. The overcharge guard stops
any automatic charge at 99%. Every automatic charge sets the inverter's charge cut-off from the LiFePO4 curve
(never above 3.60 V per cell).

### Night charge is optional

The night window (23:00–07:00, `night_start_hour` / `night_end_hour`) is when the battery is
refilled for the day ahead; a two-zone meter only makes that cheaper. With a single rate, either
keep it (each day starts with the reserve in place) or turn off `Automation: Night Charge`. Then
the grid charges only under the hold band, after an outage, below 15% and on Manual Charge, at
any hour, and the sun does the rest. The night bump automation and the outage plan count on the
night charge: without it, skip `automation.yaml` and `outage-plan.yaml`. The outage-schedule
automation still works.

### When the grid comes back

After an outage the line is often weak and unstable: the neighbours switch their loads and
chargers on at once. The firmware brings the load onto it in steps:

1. **Stage 0, 2 min (10 min after an outage of 3 h or more):** the house stays on the battery
   while the line settles (only if SoC is above 25%; in Backup above 70%). After a long outage the
   whole street switches on at once, and the line often drops again in the first minutes.
2. **Stage 1:** the house goes on the grid, the charger stays off.
3. **Stage 2:** 5 A once the voltage has held at 205 V or more for 3 min and is not sliding
   (no more than 4 V under the 5-min maximum).
4. **Ramp:** +5 A per minute while the voltage is 215 V or more.

While charging: under 207 V the current steps down 5 A; under 200 V charging pauses for at
least 10 min; under 195 V the house too goes on the battery for 10 min. A loss soon after a return doubles the
waits for the next return: stage 0 up to 10 min, stage 2 up to 10 min, the weak-line hold up to
20 min. After an hour with no loss they reset. A grid loss mid-charge that comes back within
30 min looks like the house relay: for 30 min the current is capped at the current at the loss
minus 10 A.

### Without Home Assistant

The logic and the clock run on the ESP. With Home Assistant gone, it still charges at night,
after outages and on a breached reserve; it loses only the forecast, the schedule and the plan.
After an hour without Home Assistant the Reserve strategy holds 10 h of autonomy, since it can no
longer see the schedule. The ESP restarts itself after 10 min without Home Assistant (WiFi up),
30 min without WiFi, or 20 min without inverter data; after two restarts that did not help, at
most once every 3 h.

### Examples

Numbers from the reference install (190 Ah, 16S, Reserve strategy, `Target Charge Current` 30 A).

**An ordinary winter day, no outages.** The reserve is ~45% by day, so the hold band is 35–45%.
The battery carries the house until 45%; below that the grid carries it and the sun refills the
battery. At 22:00 the forecast leaves 1.5 kWh of tomorrow's need uncovered: the bump is +15% of
the 9.7 kWh pack. At 23:00 the night target is the peak reserve 66% + 5% + 15% = 86%, and the
grid charges to it, 5 A rising to 30 A. At 07:02 the night charge ends.

**A heavy day** (9 Nov). Outages 05:30–13:00 and 16:00–22:00, 13.5 h in 24 h; 2.2 kWh of sun
against 6.4 kWh of load.
- The bump is the maximum +40%, so the night target hits the 90% ceiling.
- The outage plan says 84% is needed at 05:30; with 12 h or more of outages the night plan is
  100%, so the night charges to 100%.
- The schedule automation sets `Reserve Autonomy Target` to 8 h (the 7.5 h block + 30 min).
- Between the blocks, 13:00–16:00, the grid carries the house, and the battery is topped up for
  the evening block if the plan says so (by day at most 90%).

**The grid comes back at 14:00 after a 4 h outage**, SoC 30%, reserve 45%.
- 14:00–14:10: the house stays on the battery (stage 0, 10 min after an outage of 3 h or more).
- 14:10: the house goes on the grid.
- ~14:13: 5 A, if the voltage has held 205 V or more since 14:10 and is not sliding.
- +5 A a minute while the voltage is 215 V or more: 30 A by ~14:18. On a line at 207–214 V the
  current stays where it is.
- At 45% the charge stops: by day it buys back only the reserve; the rest waits for the night rate.

**A sunny week, Sun strategy.** The reserve is ~30%, the forecast covers the day, so the bump is
0 and the night target is the peak reserve + 5%, ~40%. At 23:00 SoC is 60%: the battery runs the
house overnight down to 40%, then the grid carries the house until morning with no charge. The
sun fills the battery by day. The grid charged nothing.

**The line goes weak at 19:00 while charging at 25 A.**
- Under 207 V: −5 A every 30 s.
- Under 200 V: charging pauses for at least 10 min (20 if it repeats within an hour); the house
  stays on the grid.
- Under 195 V: the house goes on the battery too, for 10 min (20 if it repeats within an hour).
- The house returns once the line holds 198 V for 3 min. Charging starts again from 5 A after the
  pause, once the line holds 205 V for 3 min.

### What the inverter is told

The core states intent only: where the house is fed from, whether the grid may charge, the
current limit and where to stop. The adapter turns it into the inverter's own modes and writes a
register only when the inverter reports something else (inverter settings live in EEPROM). A
value changed by hand or reset by the inverter itself is put back. `R` = reserve:

| Situation | House | Grid charging |
|---|---|---|
| Day, SoC > R | battery | no |
| Day, R−10% … R | grid | no, the sun refills |
| Grid charge session | grid | yes |
| Night, SoC above target | battery | no |
| Night, at target | grid | no |
| Backup | grid | up to 90% |

How a model maps this to its modes, and its quirks: the header of its adapter and the
[adapter contract](docs/design/adapter-contract.md).

## Install

### Hardware

| Part | Details |
|---|---|
| MCU | ESP8266 Wemos D1 Mini (`boards/d1-mini.yaml`) |
| Link | MAX485 module for RS485 (MAX3232 for RS232 adapters) |
| Battery | LiFePO4 8S / 16S / 24S |

| D1 Mini | MAX485 |
|---|---|
| GPIO13 (TX) | DI |
| GPIO12 (RX) | RO |
| GPIO14 | DE + RE |

Other pins: set `tx_pin`, `rx_pin`, `flow_control_pin` in the device file.

### Device file

The firmware is a set of ESPHome packages. Your device file in the ESPHome dashboard pulls them
from GitHub at a release tag ([examples/must-pv18-remote.yaml](examples/must-pv18-remote.yaml)):

```yaml
esphome:
  name: ${device_name}

substitutions:
  device_name: blackout-inverter
  api_key: !secret api_key
  ota_password: !secret ota_password
  # battery_cells_s: "16"      # any setting from core/defaults.yaml goes here

packages:
  blackout:
    url: https://github.com/ange007/esphome-blackout-inverter
    ref: v1.0.0
    files: [boards/d1-mini.yaml, core/blackout.yaml, adapters/must-pv18.yaml]

external_components:
  - source: github://ange007/esphome-blackout-inverter@v1.0.0
    components: [blackout]

api:
  encryption:
    key: ${api_key}
  reboot_timeout: 0s   # keep control without Home Assistant

ota:
  - platform: esphome
    password: ${ota_password}

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap: { ssid: "${device_name}", password: !secret ap_password }
```

To use a local copy instead (offline, or with your own changes), put the repository into
`/config/esphome/blackout/` and use the local form, [examples/must-pv18.yaml](examples/must-pv18.yaml).

### Before the first flash

Check in the device file's `substitutions:` (full list: [docs/settings.md](docs/settings.md)):

- `battery_cells_s`: cells in series (16 = 48 V).
- `night_start_hour` / `night_end_hour`: the night window (your night-rate hours, if any); `timezone`.
- `batt_stop_dischg_v` and `critical_low_soc`: the bottom of the usable range, written to
  the inverter at boot. Keep them paired and above the BMS cut-off.
- `reserve_hours_reserve`: hours of autonomy in the Reserve strategy. Rough check:
  `R ≈ 15 + 100 × (load_W × hours / 0.87 + 27 × hours) / (Ah × cells × 3.2)`;
  200 Ah, 16S, 500 W, 6 h gives ~50%.

After the flash, set `Battery Capacity` (Ah) in Home Assistant. The reserve is computed from it.

### Updates and saved settings

The ESP8266 keeps every saved setting in one flash block, in a fixed order. A release that adds
or removes one resets the following ones to defaults. CI blocks such changes, and a release that
needs one says so in its notes; note your values before updating across it.

## Home Assistant

The device works alone. Home Assistant adds what the ESP cannot see. All files are in
[automation/](automation/), and [docs/automations.md](docs/automations.md) explains each one and what to edit:

| File | What it gives |
|---|---|
| `helpers.yaml` | package: calibrated PV forecast, the outage-plan sensor, helper entities |
| `automation.yaml` | night charge bump from tomorrow's forecast; relays the ESP's notifications |
| `outage-schedule-automation.yaml` | reserve sized to the longest outage block ahead |
| `outage-plan.yaml` + `custom_templates/outage_plan.jinja` | outage charge plan: SoC needed for the blocks ahead |
| `esp-offline.yaml` | phone alert when the ESP is gone for 10 min |
| `flash-guard.yaml` | alert when the ESP's saved settings are lost at a restart |
| `strategy-reminder.yaml` | Sunday hint to switch Sun ⇄ Reserve |
| `../information-card.yaml` | dashboard card (needs `mushroom`) |

## Writing an adapter

An adapter is an ESPHome package: the inverter's Modbus entities with the ids the contract names,
plus [adapters/common/voltronic-sync.yaml](adapters/common/voltronic-sync.yaml) with the model's
capabilities. The core calls `bi_house`, `bi_grid_permit`, `bi_charge_limit`, `bi_charge_stop`,
`bi_discharge_stop`, `bi_solar_priority`; `bi_sync` applies them. For a Voltronic-family
inverter, copy the closest adapter and change the register map. Keep `writes_enabled: "0"`
until a live unit has proved the readings. Contract and rules:
[docs/design/adapter-contract.md](docs/design/adapter-contract.md).

## Development

| Command | Checks |
|---|---|
| `python tools/esphome_check.py all` | contract checks + `esphome config` of every example |
| `python tools/esphome_check.py compile --config examples/must-pv18.yaml` | full compile (C++) |
| `python tools/helpers_test.py` | unit tests of `components/blackout/blackout_helpers.h` (g++) |
| `python tools/prefs_layout.py --config examples/must-pv18.yaml --check tools/prefs_layout.expected.txt` | saved-settings flash layout unchanged |
| `python tools/outage_plan_test.py` | the outage-plan macro on a running Home Assistant (`HASS_URL`, `HASS_TOKEN`) |

CI runs all but the last on every push.

## License

MIT, see [LICENSE](LICENSE).
