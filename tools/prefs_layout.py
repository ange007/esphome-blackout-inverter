"""Snapshot the ESP8266 preference (flash) layout of an ESPHome build.

ESPHome stores every restored value (restored numbers, restored globals,
switches/selects with a restore mode) in one flash block, in creation order.
Reordering or resizing them silently resets the owner's settings on the next
flash. This tool lists those items in creation order so two builds can be
diffed before/after a change:

    python tools/prefs_layout.py --config examples/must-pv18.yaml > before.txt
    # ... edit ...
    python tools/prefs_layout.py --config examples/must-pv18.yaml > after.txt
    diff before.txt after.txt

CI checks it against the committed baseline and fails on any change:

    python tools/prefs_layout.py --config examples/must-pv18.yaml --check tools/prefs_layout.expected.txt

The order and the set of items are exact (they are what resets settings when they
change); the sizes are an estimate (SIZE_WORDS below).

It can also read an already-generated main.cpp:

    python tools/prefs_layout.py --main-cpp path/to/main.cpp

Exit code 0 on success, 1 when --check finds a different layout. Prints the layout.
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Rough size (in 4-byte words) of a restored preference value by C++ type.
SIZE_WORDS = {
    'bool': 1, 'uint8_t': 1, 'int8_t': 1, 'char': 1,
    'uint16_t': 1, 'int16_t': 1,
    'uint32_t': 1, 'int32_t': 1, 'float': 1, 'int': 1, 'unsigned': 1,
    'double': 2, 'uint64_t': 2, 'int64_t': 2,
    'std::array': 6,
}


REPO_DIRS = ['components', 'core', 'adapters', 'boards', 'examples']


def generate_main_cpp(config_path: Path) -> Path:
    """Run `esphome compile --only-generate` in a temp dir with dummy secrets."""
    tmp = Path(tempfile.mkdtemp(prefix='prefs_layout_'))
    # Resolve against the repo root; when the config lives under the repo and
    # uses relative package includes (../core, ../adapters, ../boards,
    # ../components) the whole needed tree must be copied, not just the file.
    try:
        rel = config_path.relative_to(ROOT)
        under_repo = True
    except ValueError:
        under_repo = False

    if under_repo:
        for d in REPO_DIRS:
            src = ROOT / d
            if src.exists():
                shutil.copytree(str(src), str(tmp / d))
        dest_config = tmp / rel
        dest_config.parent.mkdir(parents=True, exist_ok=True)
        if not dest_config.exists():
            shutil.copy(str(config_path), str(dest_config))
        cfg_dir = dest_config.parent
        cfg_name = dest_config.name
        # Dummy secrets.yaml sits next to the config; also scan included yaml
        # files for !secret keys so nothing referenced recursively is missed.
        secret_keys = set(re.findall(
            r'!secret\s+(\w+)',
            config_path.read_text(encoding='utf-8')))
        for d in REPO_DIRS:
            for yf in (ROOT / d).rglob('*.yaml'):
                secret_keys.update(re.findall(
                    r'!secret\s+(\w+)', yf.read_text(encoding='utf-8', errors='replace')))
        keys = sorted(secret_keys)
    else:
        for name in [config_path.name] + [h.name for h in config_path.parent.glob('*.h')]:
            src = config_path.parent / name
            if src.exists():
                shutil.copy(str(src), str(tmp))
        cfg_text = config_path.read_text(encoding='utf-8')
        keys = sorted(set(re.findall(r'!secret\s+(\w+)', cfg_text)))
        cfg_dir = tmp
        cfg_name = config_path.name

    DUMMY = {'api_key': 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY='}
    lines = [f'{k}: "{DUMMY.get(k, "dummy-" + k)}"' for k in keys]
    (cfg_dir / 'secrets.yaml').write_text(
        '\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
    r = subprocess.run(
        ['esphome', 'compile', '--only-generate', cfg_name],
        cwd=str(cfg_dir), capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f'esphome generate failed (rc={r.returncode})')
    matches = list(tmp.rglob('main.cpp'))
    if not matches:
        raise SystemExit('main.cpp not found after generation')
    return matches[0]


def parse_layout(main_cpp: Path):
    text = main_cpp.read_text(encoding='utf-8', errors='replace')
    lines = text.splitlines()

    # Collect candidate restored items: (line_index, id, kind, type)
    items = []

    # 1. Restored globals: new(id) globals::RestoringGlobalsComponent<T>(...)
    for m in re.finditer(r'new\((\w+)\)\s+globals::RestoringGlobalsComponent<(.+?)>\(', text):
        raw = m.group(2).strip()
        # normalise to a size key
        if 'std::array' in raw:
            typ = 'std::array'
        elif raw == 'unsigned long':
            typ = 'uint32_t'
        else:
            typ = raw
        items.append((m.start(), m.group(1), 'global', typ))

    # 2. Restored numbers: any `new(id)` whose block contains set_restore_value(true).
    # Find each set_restore_value(true) and walk back to the nearest `new(...)`.
    for m in re.finditer(r'set_restore_value\(true\)', text):
        pos = m.start()
        # nearest preceding 'new(ID)'
        back = text.rfind('new(', 0, pos)
        if back == -1:
            continue
        idm = re.match(r'new\((\w+)\)', text[back:back + 80])
        if not idm:
            continue
        ident = idm.group(1)
        # avoid duplicates (global already counted)
        if any(i[1] == ident for i in items):
            continue
        # type: number stores float
        items.append((back, ident, 'number', 'float'))

    # 2b. Restored selects: TemplateSelectWithSetAction (restore_value: true in YAML).
    for m in re.finditer(r'new\((\w+)\)\s+template_::TemplateSelectWithSetAction', text):
        ident = m.group(1)
        if any(i[1] == ident for i in items):
            continue
        items.append((m.start(), ident, 'select', 'uint8_t'))

    # 3. Restored switches: set_restore_mode(switch_::SWITCH_RESTORE...) -> restored
    for m in re.finditer(r'(\w+)->set_restore_mode\(switch_::SWITCH_RESTORE_(?:DEFAULT|ALWAYS)[A-Z_]*\)', text):
        ident = m.group(1)
        if any(i[1] == ident for i in items):
            continue
        idx = m.start()
        items.append((idx, ident, 'switch', 'bool'))

    # Sort by position in file (creation/registration order).
    items.sort(key=lambda x: x[0])

    # Build canonical rows.
    rows = []
    total_words = 0
    for seq, (_, ident, kind, typ) in enumerate(items, start=1):
        size = SIZE_WORDS.get(typ, 1)
        total_words += size
        rows.append((seq, ident, kind, typ, size))

    return rows, total_words


def render(rows, total_words):
    out = []
    out.append('# ESP8266 preference (flash) layout')
    out.append(f'# restored items: {len(rows)}  estimated words: {total_words}')
    out.append('# seq  id  kind  type  size_words')
    for seq, ident, kind, typ, size in rows:
        out.append(f'{seq:4d}  {ident}  {kind}  {typ}  {size}')
    blob = '\n'.join(out) + '\n'
    digest = hashlib.sha256(blob.encode()).hexdigest()[:12]
    out.append(f'# layout_sha256: {digest}')
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', help='ESPHome config yaml (generates main.cpp)')
    ap.add_argument('--main-cpp', help='path to an already-generated main.cpp')
    ap.add_argument('--out', help='write output to file as well as stdout')
    ap.add_argument('--check', help='expected layout file; exit 1 if the layout differs')
    args = ap.parse_args()

    if args.config:
        main_cpp = generate_main_cpp(Path(args.config).resolve())
    elif args.main_cpp:
        main_cpp = Path(args.main_cpp).resolve()
    else:
        main_cpp = generate_main_cpp((ROOT / 'examples' / 'must-pv18.yaml').resolve())

    rows, total = parse_layout(main_cpp)
    rendered = render(rows, total)
    sys.stdout.write(rendered)
    if args.out:
        Path(args.out).write_text(rendered, encoding='utf-8')
    # Also report source for traceability on stderr.
    sys.stderr.write(f'source: {main_cpp}\n')
    if args.check:
        expected = Path(args.check).read_text(encoding='utf-8').replace('\r\n', '\n')
        if expected != rendered:
            sys.stderr.write(f'LAYOUT CHANGED against {args.check}: the restored settings of '
                             f'existing devices would reset on the next flash. See AGENTS.md.\n')
            sys.exit(1)
        sys.stderr.write(f'layout matches {args.check}\n')


if __name__ == '__main__':
    main()