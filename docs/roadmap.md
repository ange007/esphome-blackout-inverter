# Roadmap

blackout-inverter is one charging brain for a home battery under rolling blackouts (reserve,
outage plan, night tariff, weak-grid and relay protection, self-heal), on ESPHome, driving an
inverter through a small adapter.

## Done

- **Core / adapter split.** The logic of a single-inverter config (MUST PV18) became an
  inverter-neutral core that states intent through a fixed contract
  ([design/adapter-contract.md](design/adapter-contract.md)); the MUST is the reference
  adapter. The flash layout of restored settings stayed exactly as it was, so existing
  installs keep their settings across the change (checked in CI).
- **Voltronic-family adapters**: MUST PV18 (tested), Growatt SPF, SRNE, PowMr SPF (untested,
  writes disabled), with one shared contract surface and capability flags (solar aim, a
  utility-only mode, SoC- or voltage-based stops).
- **Installation as packages**: a ~40-line device file pulls the core, an adapter and a board
  from GitHub at a release tag; the same tree works as local packages.
- **Home Assistant side**: forecast helpers, the outage charge plan
  ([design/outage-charge-plan.md](design/outage-charge-plan.md)), notifications, a flash
  guard, a dashboard card.

## Next

1. **Verify the untested adapters on live units** and enable their writes, one at a time
   (needs owners of those inverters; issues welcome).
2. **Axpert / Voltronic over PI30** through ESPHome's `pipsolar` component: the same control
   model as the Modbus adapters, a different transport.
3. **ESP32 board** package (the helpers already build without the ESP8266 RTC calls).
4. **Home Assistant side as parameters**: the automations take the device name as an input
   (blueprints or a package), instead of a find-and-replace of `blackout_inverter_`.
5. **Other inverter classes**: time-of-use hybrids (Deye/Sunsynk, Luxpower — charge windows
   with SoC targets, export, no SBU/SUB) and portable power stations (AC charging on/off,
   charge power and limit, house always passed through). Each needs capability flags the core
   does not branch on yet; the contract was written so the core only gains such branches.

## Constraints every change keeps

- **Flash layout**: restored settings are laid out in creation order on the ESP8266; a change
  to the set or the order resets them on existing devices. `tools/prefs_layout.expected.txt`
  is the contract; changing it is a breaking release.
- **Physical consequences**: an adapter with a wrong register can command an inverter wrongly.
  Untested adapters ship with writes disabled.
- **ESP8266 budget**: RAM ~65%, flash ~53% on the reference device.
