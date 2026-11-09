"""Validate (or compile) a device config without real secrets.

secrets.yaml is gitignored, so `esphome config` fails in a fresh checkout. This copies the
repository tree into a temp dir, writes dummy secrets next to the config and runs ESPHome
there. Nothing is flashed.

Usage:
    python tools/esphome_check.py                         # config, examples/must-pv18.yaml
    python tools/esphome_check.py compile                 # compile it (slow on the first run)
    python tools/esphome_check.py [config|compile] --config examples/srne.yaml
    python tools/esphome_check.py all                     # config for every local example

Before ESPHome runs, two contract checks (docs/design/adapter-contract.md):
- the `bi_*` scripts of every adapter are synchronous (no delay / wait_until);
- no MUST option string appears in core/ (the core states intent, adapters translate).
`*-remote.yaml` examples pull packages from GitHub and are left out of `all`.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = 'examples/must-pv18.yaml'
# Copied with their structure: examples include ../core, ../adapters, ../boards, ../components.
REPO_DIRS = ['components', 'core', 'adapters', 'boards', 'examples']

# Valid-looking placeholders: api_key must be 32 bytes of base64, passwords >= 8 chars.
DUMMY = {
    'api_key': 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=',
}

MUST_STRINGS = ('SBU (Solar/battery/utility)', 'SUB (Solar/utility/battery)',
                'UTI (Utility only)', 'SNU (Solar and utility)',
                'OSO (Solar only)', 'LBU (Load, Battery, Utility)',
                'BLU (Battery, Load, Utility)')


def strip_comments(text: str) -> str:
    """YAML text without `#` comments (good enough for the checks below)."""
    return '\n'.join(re.sub(r'(^|\s)#.*$', '', line) for line in text.splitlines())


def contract_checks() -> int:
    """Returns the number of failures."""
    fails = 0
    for adapter_path in sorted((ROOT / 'adapters').glob('*.yaml')):
        text = strip_comments(adapter_path.read_text(encoding='utf-8'))
        start = re.search(r'^script:', text, re.M)
        if not start:
            continue
        scripts = text[start.start():]
        for m in re.finditer(r'id:\s*(bi_\w+)(.*?)(?=\n  - id:|\n\S|\Z)', scripts, re.S):
            if re.search(r'\bdelay\b|\bwait_until\b', m.group(2)):
                print(f'FAIL: {adapter_path.name}: script {m.group(1)} has delay/wait_until')
                fails += 1
    for p in sorted((ROOT / 'core').glob('*.yaml')):
        text = strip_comments(p.read_text(encoding='utf-8'))
        for s in MUST_STRINGS:
            if s in text:
                print(f'FAIL: core/{p.name} names the MUST option {s!r}')
                fails += 1
    print('contract checks: ' + (f'{fails} failed' if fails else 'passed'))
    return fails


def dummy_secrets(cfg_path: Path) -> str:
    """Dummy secrets.yaml text covering every !secret in cfg_path."""
    keys = sorted(set(re.findall(r'!secret\s+(\w+)', cfg_path.read_text(encoding='utf-8'))))
    return ''.join(f'{k}: "{DUMMY.get(k, "dummy-" + k)}"\n' for k in keys)


def run_one(cfg_rel: str, action: str) -> int:
    """Validate or compile one config in a temp copy of the tree. Returns ESPHome's rc."""
    cfg_path = ROOT / cfg_rel
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for d in REPO_DIRS:
            if (ROOT / d).exists():
                shutil.copytree(ROOT / d, tmp_path / d)
        dest = tmp_path / cfg_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy(cfg_path, dest)
        (dest.parent / 'secrets.yaml').write_text(dummy_secrets(cfg_path), encoding='utf-8')
        return subprocess.call(['esphome', action, dest.name], cwd=str(dest.parent))


def main() -> int:
    args = sys.argv[1:]
    if 'all' in args:
        if contract_checks():
            return 1
        configs = sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                         for p in (ROOT / 'examples').glob('*.yaml')
                         if not p.name.endswith('-remote.yaml'))
        fails = 0
        for cfg in configs:
            print(f'=== {cfg} ===')
            if run_one(cfg, 'config') != 0:
                fails += 1
        print(f'all: {"FAILED" if fails else "OK"} ({len(configs) - fails}/{len(configs)} valid)')
        return 1 if fails else 0

    cfg_rel = DEFAULT_CONFIG
    if '--config' in args:
        i = args.index('--config')
        cfg_rel = args[i + 1]
        del args[i:i + 2]
    action = args[0] if args else 'config'
    if action not in ('config', 'compile'):
        print(f'unknown action {action!r}; use "config", "compile" or "all"')
        return 2
    if contract_checks():
        return 1
    return run_one(cfg_rel, action)


if __name__ == '__main__':
    sys.exit(main())
