#pragma once

// ============================================================================
// blackout-inverter: inverter-neutral helpers for the core and the adapters.
//
// The LiFePO4 curve and the charge-stop voltage, the reserve and outage-plan
// maths, the learned load profile, RTC-held state and RegSync (write tracking
// for adapters). Kept out of the YAML lambdas so there is one copy of each and
// the big interval lambda stays small enough for the ESP8266 to compile.
// ESPHome includes every header of a loaded component, so lambdas see this as
// soon as the device config has a `blackout:` key (see boards/*.yaml).
// ============================================================================

#include <array>
#include <cmath>
#include <cstdint>
#include <sys/time.h>
#ifdef USE_ESP8266
#include <user_interface.h>  // system_rtc_mem_read/write (ESP8266 SDK)
#else
// No user RTC memory on other platforms: the RTC-held clock and state simply start empty
// after every restart (the callers already handle "nothing stored").
static inline bool system_rtc_mem_read(uint32_t, void *, uint32_t) { return false; }
static inline bool system_rtc_mem_write(uint32_t, const void *, uint32_t) { return false; }
#endif

// The hourly load profile global is declared as std::array<uint8_t, 24> in the YAML.
// This header is included near the top of main.cpp, so pulling in <array> here makes
// sure the type is complete by the time the globals are constructed. A raw uint8_t[24]
// cannot be used: RestoringGlobalsComponent takes its initial value by value and the
// array decays to a pointer.

namespace blackout {

// Learned hourly load profile is stored as uint8_t to stay small in flash-backed
// preferences. One step = 20 W, so the representable range is 0..5100 W.
static const float HOURLY_LOAD_STEP_W = 20.0f;

// LiFePO4 SoC -> resting voltage approximation (discharge curve).
// Per-cell reference points:
//   100% = 3.650V  90% = 3.500V  50% = 3.300V  20% = 3.200V  10% = 3.100V  0% = 2.800V
// WARNING: LiFePO4 only. AGM / Li-Ion / NMC need a different curve.
static inline float soc_to_voltage(float soc, float cells) {
  if (soc >= 100.0f) return cells * 3.650f;
  if (soc >= 90.0f) return cells * (3.500f + (soc - 90.0f) * 0.015f);
  if (soc >= 50.0f) return cells * (3.300f + (soc - 50.0f) * 0.005f);
  if (soc >= 20.0f) return cells * (3.200f + (soc - 20.0f) * 0.00333f);
  if (soc >= 10.0f) return cells * (3.100f + (soc - 10.0f) * 0.010f);
  return cells * (2.800f + soc * 0.030f);
}

// Inverse of soc_to_voltage: what SoC a pack voltage corresponds to.
//
// Used to find where the inverter actually stops discharging, because that — not the
// config's own emergency threshold — is the real bottom of the usable range.
//
// WARNING: between roughly 20% and 50% the curve is almost flat (3.200 -> 3.300 V/cell
// over 30% of capacity), so a 0.05 V/cell error here is a 15% SoC error. Treat the
// result as a conservative bound, not a measurement.
static inline float voltage_to_soc(float v, float cells) {
  if (cells <= 0.0f || std::isnan(v)) return 0.0f;
  const float pc = v / cells;
  if (pc >= 3.650f) return 100.0f;
  if (pc >= 3.500f) return 90.0f + (pc - 3.500f) / 0.015f;
  if (pc >= 3.300f) return 50.0f + (pc - 3.300f) / 0.005f;
  if (pc >= 3.200f) return 20.0f + (pc - 3.200f) / 0.00333f;
  if (pc >= 3.100f) return 10.0f + (pc - 3.100f) / 0.010f;
  const float s = (pc - 2.800f) / 0.030f;
  return s > 0.0f ? s : 0.0f;
}

// Energy-use mode (modbus 20109) for a grid charge session.
//
// SUB + SNU does not grid-charge while the solar charger is running. In 14 days of
// history not one session drew from the grid with PV above ~50 W, and on 2 Nov a manual
// session sat at SUB/SNU/20 A for 30 min with the grid carrying only the house load
// (PV ~170 W). UTI with the same settings took ~1.4 kW straight away. After dark and in
// an outage SUB stays: that is where every working session ran, and without sun or grid
// UTI has nothing to add.
static inline const char *charge_energy_mode(bool night, bool grid_present) {
  return (!night && grid_present) ? "UTI (Utility only)" : "SUB (Solar/utility/battery)";
}

// Highest charge cut-off (modbus 20119) this config ever writes: 3.60 V/cell, 57.6 V
// on 16S — the pack's absorb voltage, and where it reads 100%.
//
// NOT 3.65 V/cell. The inverter rejects 58.4 V outright: in ten days of history 2570
// writes of it never survived a single read-back, and the register kept whatever the
// last charge session had set. So "reset to the safe maximum" after a session actually
// left the cut-off parked at that session's target — 54.2 V after a night charge to
// ~65% — and the sun could not finish the pack for the rest of the day. 57.9 V was
// accepted, so the limit sits somewhere just under 58.4; 3.60 stays clear of it.
static const float CELL_V_MAX = 3.600f;

static inline float max_stop_voltage(float cells) { return cells * CELL_V_MAX; }

// Clamp a pack voltage into the window [3.00, CELL_V_MAX] V/cell.
// NaN resolves to the maximum so a bad read can never lower the cut-off below what
// the inverter needs to finish a charge.
static inline float clamp_pack_voltage(float v, float cells) {
  const float v_max = max_stop_voltage(cells);
  const float v_min = cells * 3.000f;
  if (std::isnan(v)) return v_max;
  if (v > v_max) return v_max;
  if (v < v_min) return v_min;
  return v;
}

// Charge cut-off voltage (modbus 20119) for a target SoC. `buffer_soc` compensates
// for the voltage rise under charge current — the curve above is a resting curve,
// so a pack charging at 20 A sits higher than its true SoC would suggest.
//
// The buffer fades out between 80% and 90%. Up there the curve is steep and the BMS
// recalibrates to 100% as soon as it sees ~3.58 V/cell: a 95% target plus the buffer
// came out at 57.4-57.6 V and every such night ended in a full charge. The SoC stop
// rule ends the session at the target anyway; the voltage only has to be a backstop
// that does not itself fill the pack.
static inline float charge_stop_voltage(float target_soc, float cells, float buffer_soc = 3.0f) {
  if (target_soc > 80.0f) {
    float fade = (90.0f - target_soc) / 10.0f;
    if (fade < 0.0f) fade = 0.0f;
    buffer_soc *= fade;
  }
  float soc = target_soc + buffer_soc;
  if (soc > 100.0f) soc = 100.0f;
  if (soc < 0.0f) soc = 0.0f;
  return clamp_pack_voltage(soc_to_voltage(soc, cells), cells);
}

// Expected AC load energy (Wh) over the next `hours`, starting at `from_hour`.
// `profile[h]` is the learned average load for hour-of-day h in HOURLY_LOAD_STEP_W
// units; a 0 entry means "not learned yet" and falls back to `fallback_w`.
// Wraps across midnight.
static inline float expected_load_wh(int from_hour, float hours, const uint8_t *profile,
                                     float fallback_w) {
  if (std::isnan(fallback_w) || fallback_w < 0.0f) fallback_w = 0.0f;
  // Reject non-finite or absurd input outright. `hours` ultimately comes from a number
  // entity HA can write to (Reserve Autonomy Target), and this loop has no other bound
  // on it: if it were ever Infinity, `left -= slice` never reduces it
  // (Infinity - 1.0f == Infinity) and this call never returns. That hung the ESP8266's
  // main loop for real once — ping still answered (handled below the Arduino loop), but
  // the api/web_server ports went dead and stayed dead, because the device never got
  // back around to servicing them. Capped at a week of hours as a sanity ceiling.
  if (!(hours > 0.0f) || hours > 168.0f) return 0.0f;

  float total = 0.0f;
  float left = hours;
  int h = from_hour;
  while (left > 0.0f) {
    const float slice = left > 1.0f ? 1.0f : left;
    const uint8_t raw = profile[((h % 24) + 24) % 24];
    const float w = raw > 0 ? raw * HOURLY_LOAD_STEP_W : fallback_w;
    total += w * slice;
    left -= slice;
    h++;
  }
  return total;
}

// Worst-case AC load energy (Wh) over any `hours`-long window in the coming day.
//
// The plain expected_load_wh() answers "what if the power goes out right now", which is
// the right question during the day but the wrong one at 01:00: the quiet night hours
// give a small figure, the battery gets charged to match, and then the evening peak
// arrives with nothing cheap left to buy. Night charging sizes itself off this instead.
static inline float peak_load_wh(float hours, const uint8_t *profile, float fallback_w) {
  float worst = 0.0f;
  for (int start = 0; start < 24; start++) {
    const float wh = expected_load_wh(start, hours, profile, fallback_w);
    if (wh > worst) worst = wh;
  }
  return worst;
}

// Age of a value written from HA, for a TTL that has to survive restarts. The epoch stamp
// (kept in RTC memory) is used when both it and the clock are known; otherwise the millis() stamp,
// which a restart resets — the old behaviour, a fresh TTL after every boot.
static inline unsigned long override_age_ms(uint32_t set_epoch, uint32_t now_epoch,
                                            unsigned long set_ms, unsigned long now_ms) {
  if (set_epoch != 0 && now_epoch >= set_epoch) {
    uint32_t s = now_epoch - set_epoch;
    return s >= 4000000UL ? 0xFFFFFFFFUL : s * 1000UL;  // saturate instead of wrapping
  }
  return now_ms - set_ms;
}

// Hours of autonomy the reserve must cover. A positive `override_h` (the Reserve
// Autonomy Target number, settable from HA — e.g. off a published outage schedule)
// wins over the strategy's own preset, but only while it is fresh.
//
// The freshness check is what stops a dead controller from leaving the device stuck.
// If Home Assistant writes "8 hours" because tonight's block is long and then goes
// down, nothing would ever lower it again: the reserve would sit pinned near its
// ceiling and the house would run off the grid indefinitely. After `ttl_ms` without an
// update the device falls back to the strategy preset and looks after itself.
//
// `age_ms` comes from override_age_ms(): from the last write, surviving restarts
// through its RTC epoch stamp.
static inline float effective_reserve_hours(float override_h, unsigned long age_ms,
                                            unsigned long ttl_ms, uint8_t strategy,
                                            float sun_h, float reserve_h) {
  // `override_h > 0.0f` alone does not reject Infinity — isnan(inf) is false and
  // inf > 0 is true, so a garbage value here used to sail straight through and become
  // the `hours` that hangs expected_load_wh() forever (see the comment there). The
  // number entity's own min/max (0..12) constrain the UI slider, not every possible
  // write to it, so this function re-validates rather than trusting the caller.
  bool sane = !std::isnan(override_h) && override_h > 0.0f && override_h <= 48.0f;
  if (sane && age_ms < ttl_ms) return override_h;
  return strategy == 0 ? sun_h : reserve_h;
}

// Night Charge Bump as it applies right now: its value while it is fresh, 0 once stale.
//
// The bump is restored across reboots — this ESP reboots several times a night, and a
// non-restored bump was simply lost to the first one (00:45 on 23 Sep: +8% → 0). But a
// restored value with nobody left to clear it is the opposite failure: HA writes it at
// 22:00 and zeroes it at 07:05, and if HA dies in between, a restored +40% would be
// bought every night forever. The TTL bounds that to the one night it was written for.
//
// Epoch seconds, not millis(): the stamp has to survive the reboot too. No clock or
// no stamp → no bump, which is the safe direction.
static inline float effective_night_bump(float bump, uint32_t set_epoch, uint32_t now_epoch,
                                         uint32_t ttl_s) {
  if (std::isnan(bump) || bump <= 0.0f) return 0.0f;
  if (set_epoch == 0 || now_epoch < set_epoch) return 0.0f;
  if (now_epoch - set_epoch >= ttl_s) return 0.0f;
  return bump > 40.0f ? 40.0f : bump;
}

// Outage Plan SoC from Home Assistant, if it is still fresh. Same TTL rule as the night
// bump: HA rewrites it every 30 min, so a plan nobody refreshed (HA gone) expires instead
// of being charged for forever. No clock or no stamp -> no plan, the safe direction.
static inline float effective_plan_soc(float plan, uint32_t set_epoch, uint32_t now_epoch,
                                       uint32_t ttl_s) {
  if (std::isnan(plan) || plan <= 0.0f) return 0.0f;
  if (set_epoch == 0 || now_epoch < set_epoch) return 0.0f;
  if (now_epoch - set_epoch >= ttl_s) return 0.0f;
  return plan > 100.0f ? 100.0f : plan;
}

// Night charge target with the outage plan on top. The everyday target keeps its cap;
// only the plan may take the night past it, up to 100%, and it only ever raises.
static inline float night_target_with_plan(float target, float cap, float plan) {
  if (target > cap) target = cap;
  return plan > target ? plan : target;
}

// Exponential moving average whose state survives a reboot.
//
// ESPHome's exponential_moving_average filter restarts from the first sample after
// boot. For a 24-hour average on a device that reboots every hour or two that means
// the "daily" figure is really just the last hour — and the reserve is sized off it.
// `saved` is a flash-backed copy updated only when the average moves by `save_step`,
// so flash sees a write every hour or so, not every five minutes.
static inline float ema_step(float &ema, uint16_t &saved, float sample, float alpha,
                             float save_step) {
  if (std::isnan(ema)) ema = saved > 0 ? (float) saved : sample;
  ema += alpha * (sample - ema);
  if (std::fabs(ema - (float) saved) >= save_step) {
    float s = ema + 0.5f;
    if (s < 0.0f) s = 0.0f;
    if (s > 65535.0f) s = 65535.0f;
    saved = (uint16_t) s;
  }
  return ema;
}

// Hours the NIGHT charge sizes itself for: the larger of the schedule override and the
// strategy preset.
//
// The override alone can be smaller than the preset — a 4 h block in the schedule asks
// for 4.5 h while Reserve holds 6 h — and emergency outages arrive with no schedule and
// usually before the emergency flag does, so the pack meets them with whatever it
// already has. Buying the larger figure at night keeps that floor for the price of the
// cheap rate. The daytime logic keeps effective_reserve_hours(): raising its target
// would turn the extra hours into day-tariff rebuilds.
static inline float night_reserve_hours(float override_h, unsigned long age_ms,
                                        unsigned long ttl_ms, uint8_t strategy,
                                        float sun_h, float reserve_h) {
  const float preset = strategy == 0 ? sun_h : reserve_h;
  const float eff = effective_reserve_hours(override_h, age_ms, ttl_ms, strategy, sun_h, reserve_h);
  return eff > preset ? eff : preset;
}

// Reserve hours while Home Assistant is out of reach.
//
// HA is where the outage schedule and the emergency flag come from. With it gone the
// device cannot know when the next outage is, so under the Reserve strategy it assumes
// the worst rather than the preset. Sun keeps its preset (it is the "I expect no
// outages" setting) and Backup already holds a fixed high SoC.
static inline float ha_absent_hours(float hours, bool ha_absent, uint8_t strategy,
                                    float worst_h) {
  if (ha_absent && strategy == 1 && worst_h > hours) return worst_h;
  return hours;
}

// Wall clock kept across restarts in RTC user memory.
//
// Without Home Assistant and without internet a restart leaves the ESP with no time
// at all, and everything clock-bound stops: the night charge, the night hold, the
// hourly profile. On 1-2 Nov HA and the router were down for 15 h; the WiFi self-heal
// restarted the device every 30 min and every restart threw the clock away, so the
// night charge never ran. RTC memory survives software restarts and watchdog resets
// (not a power cut), costs nothing to write, and does not wear like flash.
//
// Blocks 171-173: the top of ESPHome's RTC preference region (96-173), which this config
// leaves empty because every preference lives in flash; 174-191 belong to the crash
// handler. The check word keeps garbage — or the region's first use by something else —
// from ever being taken for a time.
struct RtcClock {
  uint32_t magic;
  uint32_t epoch;
  uint32_t check;
};
static const uint32_t RTC_CLOCK_MAGIC = 0x434C4B31;  // "CLK1"
static const uint8_t RTC_CLOCK_BLOCK = 171;

static inline void rtc_clock_save(uint32_t epoch) {
  RtcClock r{RTC_CLOCK_MAGIC, epoch, RTC_CLOCK_MAGIC ^ epoch ^ 0xFFFFFFFFu};
  system_rtc_mem_write(RTC_CLOCK_BLOCK, &r, sizeof(r));
}

// 0 when there is nothing trustworthy stored.
static inline uint32_t rtc_clock_load() {
  RtcClock r{};
  if (!system_rtc_mem_read(RTC_CLOCK_BLOCK, &r, sizeof(r))) return 0;
  if (r.magic != RTC_CLOCK_MAGIC) return 0;
  if (r.check != (RTC_CLOCK_MAGIC ^ r.epoch ^ 0xFFFFFFFFu)) return 0;
  if (r.epoch < 1700000000u) return 0;
  return r.epoch;
}

// Small state that has to survive restarts, kept in RTC memory next to the clock rather
// than in flash. A new restored global would shift the flash slots of every switch,
// number and select registered after the globals (they reset to defaults after the flash,
// as on 3 Nov), and these values change often enough to wear the flash sector.
// Survives software restarts and watchdog resets, not a power cut — then the callers
// fall back to their pre-RTC behaviour.
//
// Blocks 167-170, just below the clock (171-173), in the same unused RTC preference region.
struct RtcState {
  uint32_t magic;
  uint32_t reserve_set_epoch;  // epoch of the last Reserve Autonomy Target write, 0 = unknown
  uint32_t packed;             // bit 0: session_night; bits 8-15: modbus_heal_streak
  uint32_t check;
};
static const uint32_t RTC_STATE_MAGIC = 0x53544131;  // "STA1"
static const uint8_t RTC_STATE_BLOCK = 167;

static inline void rtc_state_save(uint32_t reserve_set_epoch, bool session_night, uint8_t modbus_streak) {
  const uint32_t packed = (session_night ? 1u : 0u) | ((uint32_t) modbus_streak << 8);
  RtcState r{RTC_STATE_MAGIC, reserve_set_epoch, packed,
             RTC_STATE_MAGIC ^ reserve_set_epoch ^ packed ^ 0xFFFFFFFFu};
  system_rtc_mem_write(RTC_STATE_BLOCK, &r, sizeof(r));
}

// false when there is nothing trustworthy stored (first boot, after a power cut).
static inline bool rtc_state_load(uint32_t &reserve_set_epoch, bool &session_night, uint8_t &modbus_streak) {
  RtcState r{};
  if (!system_rtc_mem_read(RTC_STATE_BLOCK, &r, sizeof(r))) return false;
  if (r.magic != RTC_STATE_MAGIC) return false;
  if (r.check != (RTC_STATE_MAGIC ^ r.reserve_set_epoch ^ r.packed ^ 0xFFFFFFFFu)) return false;
  reserve_set_epoch = r.reserve_set_epoch;
  session_night = (r.packed & 1u) != 0;
  modbus_streak = (uint8_t) ((r.packed >> 8) & 0xFFu);
  return true;
}

// Fold one hour's measured average into the profile. First sample seeds the bucket,
// later ones blend at 20% so the profile follows real usage over roughly five days
// without a single odd day throwing it off.
static inline void learn_hourly_load(uint8_t *profile, int hour, float avg_w) {
  if (std::isnan(avg_w) || avg_w < 0.0f) return;
  const int idx = ((hour % 24) + 24) % 24;
  const uint8_t old = profile[idx];
  const float blended = old > 0 ? (old * HOURLY_LOAD_STEP_W) * 0.8f + avg_w * 0.2f : avg_w;
  float steps = blended / HOURLY_LOAD_STEP_W + 0.5f;
  if (steps < 0.0f) steps = 0.0f;
  if (steps > 255.0f) steps = 255.0f;
  profile[idx] = (uint8_t) steps;
}

// What the battery actually has to give up to deliver `ac_wh` over `hours`.
//
// Conversion loss is a multiplier, but the inverter's own consumption is a constant:
// it draws its idle power for as long as it is switched on, whether the house is using
// anything or not. The values in use (inverter_efficiency / inverter_idle_w in the YAML,
// 0.87 and 27 W) come from a two-point kettle test and long battery-only runs; see the
// comment on those substitutions. An earlier regression gave 0.817 / 69 W, but it read
// the inverter's apparent power as real watts and missed samples.
//
// Leaving the idle term out understates the drain most at low loads — exactly the
// overnight hours where autonomy matters.
static inline float ac_to_dc_wh(float ac_wh, float hours, float efficiency, float idle_w) {
  if (std::isnan(efficiency) || efficiency < 0.5f) efficiency = 0.85f;
  if (std::isnan(idle_w) || idle_w < 0.0f) idle_w = 0.0f;
  if (std::isnan(ac_wh) || ac_wh < 0.0f) ac_wh = 0.0f;
  return ac_wh / efficiency + idle_w * hours;
}

// Reserve SoC (%) holding `need_dc_wh` of battery energy above `floor_soc`.
// Feed it the output of ac_to_dc_wh — the conversion belongs at the call site, where
// the number of hours is known.
static inline float reserve_target_soc(float need_dc_wh, float capacity_ah, float v_nom,
                                       float floor_soc, float soc_min, float soc_max) {
  if (capacity_ah <= 0.0f || v_nom <= 0.0f || std::isnan(need_dc_wh)) return soc_min;

  const float pack_wh = capacity_ah * v_nom;
  float soc = floor_soc + 100.0f * need_dc_wh / pack_wh;

  if (soc < soc_min) soc = soc_min;
  if (soc > soc_max) soc = soc_max;
  return soc;
}

// Write tracking for one inverter register, used by the adapters' reconcile.
//
// The adapter compares what the core wants with what the inverter reports and writes
// only when they differ. The read-back cannot be trusted right after a write: an ESPHome
// Modbus number publishes the value it has just queued as its state, and a select shows a
// write only after its next poll. So:
// - after a write, judge nothing for `settle_ms` (two polls) — unless a DIFFERENT value is
//   wanted now: then the stale read-back may look right while the inverter is about to get
//   the old write, so write at once;
// - after `max_attempts` writes the read-back never confirmed, back off; each further
//   backoff doubles, up to 6 h (a value the inverter refuses must not cost thousands of
//   EEPROM writes a day — the MUST silently rejects 58.4 V and stores 0 A as 80 A);
// - a confirmed read-back starts over; a new wish restarts the attempt count.
// One per register: a register the inverter refuses must not hold up the others.
struct RegSync {
  float written = NAN;         // value of the last write
  uint32_t written_ms = 0;     // 0 = no write waiting to show up
  uint32_t backoff_until = 0;  // 0 = no backoff
  uint32_t backoff_ms = 600000UL;
  uint8_t attempts = 0;

  // New wish: count attempts afresh (the settle window of the last write stays).
  void reset() { backoff_until = 0; attempts = 0; }

  // true when `want` should be written now; the caller then writes.
  bool due(bool matches, float want, uint32_t now, uint32_t settle_ms = 75000UL,
           uint8_t max_attempts = 3) {
    const bool settling = written_ms != 0 && now - written_ms < settle_ms;
    if (settling) {
      if (want == written) return false;  // ours, still on its way
      return write_(want, now, max_attempts);
    }
    if (matches) {
      attempts = 0; backoff_until = 0; backoff_ms = 600000UL; written_ms = 0;
      return false;
    }
    if (backoff_until != 0) {
      if ((int32_t) (now - backoff_until) < 0) return false;
      backoff_until = 0;
      attempts = 0;
    }
    return write_(want, now, max_attempts);
  }

 private:
  bool write_(float want, uint32_t now, uint8_t max_attempts) {
    attempts++;
    written = want;
    written_ms = now == 0 ? 1 : now;
    if (attempts >= max_attempts) {
      backoff_until = now + backoff_ms;
      backoff_ms = backoff_ms >= 10800000UL ? 21600000UL : backoff_ms * 2;  // up to 6 h
    }
    return true;
  }
};

}  // namespace blackout
