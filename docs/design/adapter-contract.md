# The core ↔ adapter contract

How the inverter-neutral core (`core/`) drives an inverter through an adapter
(`adapters/<model>.yaml`). The core decides; the adapter only translates and keeps the
inverter where the core wants it.

## Why it is shaped this way

The core began as one 4 000-line config for a MUST PV18 that named MUST modes (`SBU`, `SNU`,
`LBU`, ...) in 48 places and wrote registers directly in 54. The split kept every decision
and moved every register into the adapter. Three lessons from running the first version of
the split on the reference inverter for a day shaped the current contract:

- **The permission and the current are separate intents.** A first contract had one call,
  `charge(amps)`, with 0 meaning "off". Off then wrote 0 A to the current register, which the
  MUST stores as its 80 A maximum; the read-back never matched, and the reconcile rewrote it
  ~330 times a day (inverter EEPROM). The watchdog re-asserting the current also re-granted
  the permission, so a charge paused on a sagging line came back on.
- **One write tracker per register.** A single "one write per 30 s" gate made a pause below
  200 V wait behind an unrelated write, and a register the inverter refused held up the
  others through a shared backoff.
- **Batch, then apply.** Each call writing at once sent intermediate states to the inverter
  (SUB, then UTI a moment later). The scripts now only record; one `bi_sync` per control pass
  writes what is left different.

## Actions

ESPHome scripts the adapter defines (for the Voltronic family:
`adapters/common/voltronic-sync.yaml`). They only store the wish.

| script | parameters | meaning |
|---|---|---|
| `bi_house` | `source` int: 0 battery, 1 grid | where the house is fed from while there is grid |
| `bi_grid_permit` | `allow` bool, `session` bool | grid charging allowed; `session` = a charge session is running or paused |
| `bi_charge_limit` | `amps` float | grid charge current limit, battery side; clamped to `charge_amps_start`..`charge_amps_max` |
| `bi_charge_stop` | `soc` float, % | where the inverter itself stops a charge (100 = the open ceiling) |
| `bi_discharge_stop` | `soc` float, % | where the inverter stops discharging |
| `bi_solar_priority` | `target` int: 0 load, 1 battery | where PV goes first; ignored without `cap_solar_aim` |
| `bi_sync` | — | apply: compare each register with its read-back, write the ones that differ |
| `bi_manual_amps` | `amps` float | Full Manual's sag guard only: write the current directly |

Rules:
- The core never names an inverter mode or register; `tools/esphome_check.py` fails on MUST
  option strings in `core/`.
- The main loop calls `bi_sync` on every exit (`SyncOnExit` in `core/control.yaml`); user
  actions (switches, numbers, the strategy select) call it at their end; an interval runs it
  every 30 s, so the inverter is put right even when nothing calls.
- Scripts are synchronous (no `delay` / `wait_until`), checked by `esphome_check.py`.
- Stop points are SoC. The adapter converts (the Voltronic family stops on pack voltage:
  `blackout::charge_stop_voltage`, with its charge buffer, applied once, in the adapter).

## Reconcile (`bi_sync`)

For each register with a wish, compare the wish with the inverter's read-back; if they differ,
write — through one `blackout::RegSync` per register:
- after a write, wait 75 s (two polls) for the read-back to show it before judging again;
- after 3 writes that never showed up, back off 10 min; a new wish or a matching read-back
  starts over.

Order within one pass:
1. Charging **on**: the current limit, then the permission. Charging **off**: the permission,
   then the limit. (The MUST restores 80 A after every outage; a permission ahead of the limit
   would charge at that for a poll.)
2. Energy mode, derived in the adapter: battery → SBU; grid + session + day + grid present →
   UTI where `cap_grid_only` (the MUST does not grid-charge in SUB while its solar charger
   runs); otherwise SUB.
3. Charge stop, discharge stop.
4. Solar aim; never battery-first at night (the MUST freezes in bypass).

Nothing is written while the inverter data is older than 150 s, or while `hands_off` is set
(Full Manual). Entering Full Manual, the core first hands the inverter over in a clean idle
state (mode, idle current, open cut-off, charger off — on for Backup); the adapter then forgets
every wish, so leaving Full Manual replays nothing stale. The current limit is not reconciled
while the grid is down (the MUST resets it itself); the order in 1. puts it back before the
permission on the way back.

## Readings

The adapter declares the inverter's entities with fixed ids; the core reads:
`battery_state_of_charge`, `battery_voltage_sensor`, `grid_voltage_sensor`, the load and PV
sensors, and updates the global `bi_data_ms` on every fresh SoC read (staleness, self-heal).

Read-back for the core's own decisions (from the shared sync, 10 s template sensors):

| id | meaning |
|---|---|
| `bi_ctl_grid_charge_on` | the charger may take from the grid now (anything but solar-only) |
| `bi_ctl_grid_charge_amps` | the current limit the inverter holds |
| `bi_ctl_stop_soc` / `bi_ctl_discharge_stop_soc` | the stop points it holds, as SoC |

## Capabilities

Passed by each adapter as `vars` of the shared sync package:

| var | meaning | MUST | Growatt SPF | SRNE | PowMr |
|---|---|---|---|---|---|
| `cap_solar_aim` | has a load-first / battery-first setting | 1 | 1 | 0 | 0 |
| `cap_grid_only` | use UTI for a grid-fed session by day | 1 | 0 | 0 | 0 |
| `stop_by_soc` | stop registers take SoC % (else pack voltage) | 0 | 0 | 1 | 0 |
| `writes_enabled` | write to the inverter at all | 1 | 0 | 0 | 0 |

An adapter without a setting the sync names declares an internal placeholder with that id:
ESPHome checks every `id()` in a lambda, even one compiled out.

## Flash layout

Restored settings live in the core (`core/settings.yaml`, `core/state.yaml`); adapters declare
none. `tools/prefs_layout.expected.txt` is the layout every release keeps; CI fails on a change.
See AGENTS.md.

## Not covered yet

Time-of-use hybrids (Deye/Sunsynk, Luxpower: charge windows with SoC targets, no SBU/SUB) and
portable power stations (no house-source control) need capability flags the core does not
branch on yet: see [../roadmap.md](../roadmap.md).
