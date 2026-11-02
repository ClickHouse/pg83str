#!/usr/bin/env python3
"""The decoder and emitter against the compiler: every unit at -O0..-O3 is
decoded, emitted as assembly and assembled again; the instruction sequence
must be identical. If an objdump is available, instruction boundaries are
checked against it as well.

    roundtrip.py --cc CC --out DIR"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
import x86  # noqa: E402
from elf import read as read_elf  # noqa: E402
from units import UNITS  # noqa: E402


def objdump():
    return next((t for t in ('llvm-objdump', 'objdump') if shutil.which(t)), None)


def boundaries(tool, path, section):
    out = subprocess.run([tool, '-d', '--no-show-raw-insn', '--section=' + section, str(path)], capture_output=True, text=True, check=True).stdout
    return {int(m.group(1), 16) for m in (re.match(r'\s+([0-9a-f]+):\s+\S', line) for line in out.splitlines()) if m}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    p.add_argument('--out', default='roundtrip')
    args = p.parse_args()
    cc = [*shlex.split(args.cc), *shlex.split(os.environ.get('CPPFLAGS', '')), *shlex.split(os.environ.get('CFLAGS', ''))]
    out = Path(args.out).resolve()
    tool = objdump()
    failures = 0
    for level in ('O0', 'O1', 'O2', 'O3'):
        where = out / level
        where.mkdir(parents=True, exist_ok=True)
        objects = []
        for unit, march in UNITS.items():
            obj = where / (unit + '.o')
            subprocess.run([*cc, '-' + level, '-fno-builtin', '-fno-stack-protector', '-fno-pic', '-fno-pie', *march, '-c', str(ROOT / (unit + '.c')), '-o', str(obj)], check=True)
            objects.append(obj)
            if tool:
                e = read_elf(obj)
                for s in e.sections:
                    if s.flags & 4 and s.type == 'SHT_PROGBITS' and s.size:
                        ours = {j['offset'] for j in x86.decode(s.data, 0)}
                        ref = boundaries(tool, obj, s.name)
                        if ours != ref:
                            print('%s %s %s: instruction boundaries differ from %s' % (level, unit, s.name, tool))
                            failures += 1
        r = subprocess.run([sys.executable, str(ROOT / 'dev/post.py'), '--cc', args.cc, '--roundtrip', '--check', '--out', str(where / 'post'), *map(str, objects)],
                           capture_output=True, text=True)
        sys.stdout.write(''.join('%s %s' % (level, line) for line in r.stdout.splitlines(True)))
        if r.returncode:
            sys.stdout.write(r.stderr)
            failures += 1
    print('round-trip: %s' % ('ok' if not failures else '%d failures' % failures))
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
