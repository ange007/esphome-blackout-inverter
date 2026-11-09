"""Host unit tests for components/blackout/blackout_helpers.h (no flashing, no ESPHome).

Compiles the header that ships in the firmware, with USE_ESP8266 and a stubbed
user_interface.h so the RTC code compiles too, on the host toolchain
(g++/cc/clang++) and runs assertions for the inverter-neutral math:
LiFePO4 curve, charge-stop voltage, reserve/outage-plan helpers, EMA step.

Usage:
    python tools/helpers_test.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEST_CPP = r'''
#include <cstdio>
#include <cmath>
#include <array>
#include "blackout_helpers.h"
static int fails = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); fails++; } } while (0)
#define APPROX(a, b, eps) CHECK(std::fabs((a) - (b)) < (eps))
int main() {
  using namespace blackout;
  // LiFePO4 curve endpoints (16 cells)
  APPROX(soc_to_voltage(100.0f, 16), 16 * 3.65f, 0.01f);
  APPROX(soc_to_voltage(0.0f, 16), 16 * 2.80f, 0.01f);
  // Roundtrip soc->V->soc within a few %
  for (float s = 10.0f; s <= 95.0f; s += 5.0f) {
    float v = soc_to_voltage(s, 16);
    float back = voltage_to_soc(v, 16);
    CHECK(std::fabs(back - s) < 6.0f);
  }
  // charge_stop_voltage: monotonic increasing with SoC, capped at max
  float prev = -1.0f;
  for (float s = 50.0f; s <= 100.0f; s += 10.0f) {
    float v = charge_stop_voltage(s, 16);
    CHECK(v > prev); prev = v;
  }
  CHECK(charge_stop_voltage(100.0f, 16) <= max_stop_voltage(16) + 0.01f);
  CHECK(max_stop_voltage(16) > 50.0f);
  // effective_plan_soc TTL: stale -> 0, fresh -> plan, absent -> 0
  CHECK(effective_plan_soc(80.0f, 1000, 1100, 43200) == 80.0f);
  CHECK(effective_plan_soc(80.0f, 1000, 1000 + 13 * 3600, 12 * 3600) == 0.0f);
  CHECK(effective_plan_soc(0.0f, 1000, 1100, 43200) == 0.0f);
  CHECK(effective_plan_soc(80.0f, 0, 1100, 43200) == 0.0f);
  // night_target_with_plan: plan only raises, cap respected without plan
  CHECK(night_target_with_plan(50.0f, 90.0f, 0.0f) == 50.0f);
  CHECK(night_target_with_plan(95.0f, 90.0f, 0.0f) == 90.0f);
  CHECK(night_target_with_plan(50.0f, 90.0f, 100.0f) == 100.0f);
  // ema_step: converges toward sample, doesn't jump
  float ema = NAN; uint16_t saved = 300;
  float out = ema_step(ema, saved, 500.0f, 0.01f, 20.0f);
  CHECK(!std::isnan(out) && out >= 300.0f && out <= 500.0f);
  // reserve_target_soc: bounded [floor, cap]
  float r = reserve_target_soc(1000.0f, 190.0f, 51.2f, 15.0f, 25.0f, 70.0f);
  CHECK(r >= 15.0f && r <= 70.0f);
  // expected_load_wh: zero profile + fallback = hours*fallback approx
  std::array<uint8_t, 24> prof{};
  float wh = expected_load_wh(0, 24.0f, prof.data(), 200.0f);
  CHECK(wh > 0.0f);
  // RegSync: write, trust nothing while it settles, back off after 3 unconfirmed writes
  RegSync rs;
  CHECK(rs.due(false, 5.0f, 1000));            // first mismatch -> write
  CHECK(!rs.due(true, 5.0f, 2000));            // number shows its own write: still settling
  CHECK(rs.due(false, 5.0f, 80000));           // settled, the inverter refused -> 2nd write
  CHECK(rs.due(false, 5.0f, 160000));          // 3rd write starts a 10 min backoff
  CHECK(!rs.due(false, 5.0f, 300000));         // backing off
  CHECK(rs.due(false, 5.0f, 160000 + 600001)); // backoff over -> try again
  CHECK(rs.backoff_ms == 1200000UL);           // the next backoff is twice as long
  CHECK(!rs.due(true, 5.0f, 900000));          // confirmed -> nothing, all reset
  CHECK(rs.attempts == 0 && rs.backoff_ms == 600000UL);
  // A different wish while the last write settles: the read-back is stale, write at once
  RegSync sel;
  CHECK(sel.due(false, 1.0f, 1000));           // SNU written
  CHECK(sel.due(true, 0.0f, 20000));           // OSO wanted, select still shows OSO: write
  CHECK(!sel.due(true, 0.0f, 30000));          // OSO on its way
  // Refused for a day: writes per day stay small
  RegSync bad; int writes = 0;
  for (uint32_t t = 1; t < 86400000UL; t += 15000UL) writes += bad.due(false, 58.4f, t);
  CHECK(writes < 40);
  // charge_stop_voltage(100) is the open ceiling the core asks for when idle
  APPROX(charge_stop_voltage(100.0f, 16), max_stop_voltage(16), 0.001f);
  printf(fails ? "FAILED %d\n" : "ALL PASSED\n", fails);
  return fails ? 1 : 0;
}
'''

STUB_H = r'''
#pragma once
#include <cstdint>
#include <cstddef>
static inline bool system_rtc_mem_read(uint32_t, void*, size_t) { return false; }
static inline bool system_rtc_mem_write(uint32_t, const void*, size_t) { return false; }
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='helpers_test_'))
    (tmp / 'user_interface.h').write_text(STUB_H, encoding='utf-8')
    shutil.copy(ROOT / 'components' / 'blackout' / 'blackout_helpers.h', tmp / 'blackout_helpers.h')
    (tmp / 'test.cpp').write_text(TEST_CPP, encoding='utf-8')
    cxx = shutil.which('g++') or shutil.which('c++') or shutil.which('clang++')
    if not cxx:
        print('no host C++ compiler found (install g++/clang++)')
        return 2
    exe = tmp / 'helpers_test'
    r = subprocess.run([cxx, '-std=c++17', '-DUSE_ESP8266', '-I', str(tmp), str(tmp / 'test.cpp'), '-o', str(exe)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        return 1
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
