# AGENTS.md

ESPHome firmware (blackout-inverter) that decides when a home battery charges from the grid:
an inverter-neutral core plus per-inverter adapters, and Home Assistant automations around
it. It drives real inverters, so a bad change here has physical consequences. See README.md
for the charging logic and docs/design/ for the contract and the outage plan.

## Files

| Path | What it is |
|------|------------|
| `core/*.yaml` | The logic: `defaults.yaml` (substitutions), `settings.yaml` (every restored setting), `state.yaml` (globals), `control.yaml` (the main 30 s loop, ~1100 lines), `heal.yaml` (self-heal, boot), `sensors.yaml` ([EH] sensors, status, events). `blackout.yaml` includes them in order. |
| `adapters/<model>.yaml` | One inverter: UART/Modbus, its entities with the ids the contract names, capabilities. |
| `adapters/common/voltronic-sync.yaml` | The contract surface shared by the Voltronic-family adapters: `bi_*` scripts, reconcile (`bi_sync`), read-back sensors. |
| `boards/*.yaml` | MCU, pins, logger, web server. |
| `components/blackout/` | External component: `blackout_helpers.h` (LiFePO4 curve, reserve maths, RTC state, `RegSync`). |
| `examples/*.yaml` | Device files: local form per adapter, remote form for MUST. |
| `automation/` | Home Assistant automations, helpers package, outage-plan macro. |
| `tools/` | Checks, see below. |
| `.scratch/` (gitignored) | Local one-off scripts and their output. Temporary files go here, never next to tracked files. |

The big files (`core/control.yaml`, the adapters): search first, then read with offset/limit.

## Checking changes

- Any YAML change: `python tools/esphome_check.py all` (contract checks + every example).
- Any C++ (lambdas, `blackout_helpers.h`): `python tools/esphome_check.py compile --config examples/must-pv18.yaml`;
  `config` does not compile C++. Header changes also: `python tools/helpers_test.py` (needs g++).
- Anything that might add, remove or reorder a restored item:
  `python tools/prefs_layout.py --config examples/must-pv18.yaml --check tools/prefs_layout.expected.txt`.
- The outage-plan macro: `python tools/outage_plan_test.py` against a running Home Assistant.
- Do not run bare `esphome config` on an example: `secrets.yaml` is absent; the tools write dummy ones.

## Needs explicit confirmation from the owner

- Flashing (`esphome run`, `esphome upload`, any OTA) and writing the deployed device config.
- Writing a live Home Assistant config, reloading automations or restarting Home Assistant.
- Turning `writes_enabled` on for an adapter.

## Rules

- The core states intent only: no inverter mode strings or register addresses in `core/`
  (`esphome_check.py` fails on MUST option strings there). It calls the `bi_*` scripts,
  which only record the wish, and `bi_sync` applies them — the main loop calls it on every
  exit (`SyncOnExit`), user actions call it at their end.
- Adapters write a register only when its read-back differs, through one `blackout::RegSync`
  per register. Never write a grid charge current below `charge_amps_start` (the MUST stores
  0 as 80 A); grid charging off is `bi_grid_permit(false, ...)`. On: current first, then the
  permission; off: the permission first.
- New adapters ship with `writes_enabled: "0"` until a live unit has proved the readings.
- ESP8266 keeps every restored value (`restore_value: yes` globals, restored numbers,
  switches, selects) in one flash block, in creation order: the template numbers, then the
  select, then the restored globals, then the template switches. Adding, removing or
  reordering one shifts the rest, and they come back as defaults after the flash. Adapters
  declare no restored items; new small state goes to RTC memory (`blackout::RtcState`,
  survives soft restarts, not a power cut). If a change must alter the layout, update
  `tools/prefs_layout.expected.txt` in the same change and say so in the release notes.
- ESPHome template switches run their `turn_on_action` / `turn_off_action` at every boot
  (`TemplateSwitch::setup` applies the restored state through `control()`). Guard actions
  that touch the inverter with the state they are meant to change, and give switches whose
  state lives in a global `restore_mode: DISABLED`.
- YAML 1.1 reads `on`, `off`, `yes`, `no` keys as booleans: never name a script parameter so.
- ESPHome checks every `id()` in a lambda, even inside `#if 0`: an adapter without a setting
  the shared sync names declares an internal placeholder with that id.
- Keep existing Home Assistant automation `id` values: Home Assistant ties state and traces to them.
- Never print or commit secrets. Code and comments in English; keep the existing comment
  style (why, with the measurement or incident behind a number).
- User docs come in pairs: `README.md` / `README.uk.md`, `docs/settings.md` / `.uk.md`,
  `docs/automations.md` / `.uk.md`. A change to a setting, a default or an automation updates
  both languages.
