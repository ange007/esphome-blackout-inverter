"""Render automation/custom_templates/outage_plan.jinja through HA's /api/template with fixed
inputs and check the results. Read-only for HA: /api/template renders, it changes nothing.

Needs a running Home Assistant (any instance: the macro is rendered, nothing is read from
or written to its entities), so it is a manual check, not part of CI. Run it after any change
to the macro:

    HASS_URL=http://homeassistant.local:8123 HASS_TOKEN=<long-lived token> python tools/outage_plan_test.py

HASS_ENV_FILE=<path> may point at a KEY=VALUE file with the two values instead. The fixed
scenarios are in Kyiv winter time (UTC+2), as the measured loads they use.
"""
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACRO = open(os.path.join(ROOT, 'automation', 'custom_templates', 'outage_plan.jinja'), encoding='utf-8').read()
ENV = {k: os.environ[k] for k in ('HASS_URL', 'HASS_TOKEN') if os.environ.get(k)}
if len(ENV) < 2 and os.environ.get('HASS_ENV_FILE'):
    text = open(os.environ['HASS_ENV_FILE'], encoding='utf-8').read()
    ENV = {**dict(m.groups() for m in re.finditer(r'^(HASS_URL|HASS_TOKEN)=(.*)$', text, re.M)), **ENV}
if len(ENV) < 2:
    sys.exit('set HASS_URL and HASS_TOKEN (or HASS_ENV_FILE), see the docstring')
URL = ENV['HASS_URL'].strip().strip('"').rstrip('/')
TOKEN = ENV['HASS_TOKEN'].strip().strip('"').removeprefix('Bearer ')
KYIV = datetime.timezone(datetime.timedelta(hours=2))

# Measured 27 Oct - 2 Nov, mean W per hour of day.
LOAD = [320, 269, 235, 218, 181, 580, 588, 552, 440, 165, 172, 245,
        292, 319, 342, 332, 390, 476, 531, 483, 494, 610, 673, 592]
# 3 Nov forecast x learned factor 0.66, Wh per hour.
PV_3NOV = {6: 0, 7: 16, 8: 51, 9: 115, 10: 164, 11: 172, 12: 212, 13: 190, 14: 135, 15: 68, 16: 5}
SCHED_3NOV = [('2026-11-03T06:00', '2026-11-03T10:00'), ('2026-11-03T13:00', '2026-11-03T20:30'),
              ('2026-11-03T23:30', '2026-11-04T00:00')]
PACK_WH = 190 * 51.2


def ts(s):
    return datetime.datetime.fromisoformat(s).replace(tzinfo=KYIV).timestamp()


def pv_dict(day, per_hour):
    return {'%sT%02d' % (day, h): wh for h, wh in per_hour.items()}


def events(blocks):
    return [{'start': s + ':00+02:00', 'end': e + ':00+02:00'} for s, e in blocks]


def plan(now, blocks, pv=None, load=LOAD, amps=30, emergency=False):
    tpl = MACRO + '{{ outage_plan(%r, %s, %s, %s, %r, %r, %s) }}' % (
        ts(now), json.dumps(events(blocks)), json.dumps(pv or {}), json.dumps(load),
        PACK_WH, amps, 'true' if emergency else 'false')
    req = urllib.request.Request(URL + '/api/template', data=json.dumps({'template': tpl}).encode(),
                                 headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def no_outages_means_no_plan():
    r = plan('2026-11-03T01:00', [])
    assert r['plan'] == 0, r


@case
def sunny_low_load_stays_low():
    sun = pv_dict('2026-11-03', {h: 600 for h in range(9, 16)})
    r = plan('2026-11-03T08:00', [('2026-11-03T12:00', '2026-11-03T14:00')], sun, load=[150] * 24)
    assert r['plan'] <= 30 and not r['day_grid'], r


@case
def night_3nov_needs_full_charge():
    r = plan('2026-11-03T01:00', SCHED_3NOV, pv_dict('2026-11-03', PV_3NOV))
    assert r['plan'] >= 98, r


@case
def gap_3nov_tops_up_for_the_evening_block():
    r = plan('2026-11-03T10:45', SCHED_3NOV, pv_dict('2026-11-03', PV_3NOV))
    assert 70 <= r['plan'] <= 80, r


@case
def evening_before_a_morning_block_waits_for_the_night():
    r = plan('2026-11-03T20:00', [('2026-11-04T06:00', '2026-11-04T10:00')])
    assert r['plan'] <= 30 and r['need_at_outage'] >= 40, r


@case
def long_dark_day_uses_day_grid():
    blocks = [('2026-11-03T05:00', '2026-11-03T13:00'), ('2026-11-03T15:00', '2026-11-03T23:00')]
    night = plan('2026-11-03T01:00', blocks)
    gap = plan('2026-11-03T13:30', blocks)
    assert night['plan'] >= 98 and night['day_grid'], night
    assert gap['plan'] >= 80, gap


@case
def emergency_trusts_no_future_window():
    r = plan('2026-11-03T11:00', SCHED_3NOV, pv_dict('2026-11-03', PV_3NOV), emergency=True)
    assert r['plan'] == 100 and not r['day_grid'] and r['need_now'] < 100, r


@case
def emergency_without_blocks_still_takes_the_grid():
    r = plan('2026-11-03T11:00', [], emergency=True)
    assert r['plan'] == 100, r


@case
def night_plan_is_the_need_when_the_grid_goes():
    r = plan('2026-11-03T01:00', [('2026-11-03T13:00', '2026-11-03T17:00')])
    assert r['plan'] == r['need_at_outage'] and r['plan'] > 25, r


@case
def block_in_progress_counts_only_what_is_left():
    full = plan('2026-11-03T12:00', [('2026-11-03T13:00', '2026-11-03T17:00')])
    half = plan('2026-11-03T15:00', [('2026-11-03T13:00', '2026-11-03T17:00')])
    assert half['need_now'] < full['need_now'], (full, half)


@case
def later_start_within_the_hour_needs_no_more():
    on_hour = plan('2026-11-03T10:00', [('2026-11-03T13:00', '2026-11-03T17:00')])
    mid_hour = plan('2026-11-03T10:00', [('2026-11-03T13:30', '2026-11-03T17:00')])
    assert mid_hour['need_at_outage'] <= on_hour['need_at_outage'], (on_hour, mid_hour)


@case
def block_beyond_the_horizon_is_ignored():
    r = plan('2026-11-03T01:00', [('2026-11-04T12:00', '2026-11-04T16:00')])
    assert r['plan'] == 0, r


@case
def pv_surplus_in_an_outage_keeps_the_floor():
    sun = pv_dict('2026-11-03', {h: 1000 for h in range(9, 17)})
    r = plan('2026-11-03T08:00', [('2026-11-03T11:00', '2026-11-03T14:00')], sun, load=[150] * 24)
    assert r['need_at_outage'] >= 25, r


@case
def next_outage_is_the_block_start():
    r = plan('2026-11-03T10:45', [('2026-11-03T13:30', '2026-11-03T17:00')])
    assert r['next_outage'] == ts('2026-11-03T13:30'), r


# 5 Nov: 07:00-14:00 and 17:30-00:00, 3.5 h of grid by day.
SCHED_5NOV = [('2026-11-05T07:00', '2026-11-05T14:00'), ('2026-11-05T17:30', '2026-11-06T00:00')]


@case
def heavy_day_charges_the_night_to_full():
    r = plan('2026-11-05T01:30', SCHED_5NOV, pv_dict('2026-11-05', PV_3NOV))
    assert r['plan'] == 100 and r['need_at_outage'] < 100 and r['outage_24h'] >= 12, r


@case
def heavy_day_does_not_buy_day_rate_to_full():
    r = plan('2026-11-05T14:30', SCHED_5NOV, pv_dict('2026-11-05', PV_3NOV))
    assert r['plan'] == r['need_now'] and r['plan'] < 100, r


@case
def light_night_keeps_the_need():
    r = plan('2026-11-05T01:30', [('2026-11-05T07:00', '2026-11-05T14:00')], pv_dict('2026-11-05', PV_3NOV))
    assert r['plan'] == r['need_at_outage'] and r['plan'] < 100 and r['outage_24h'] < 12, r


failed = 0
for fn in CASES:
    try:
        fn()
        print('PASS', fn.__name__)
    except Exception as e:  # a KeyError from a half-built result is a failure too
        failed += 1
        print('FAIL', fn.__name__, type(e).__name__, e)
print('%d passed, %d failed' % (len(CASES) - failed, failed))
sys.exit(1 if failed else 0)
