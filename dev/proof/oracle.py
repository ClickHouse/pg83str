#!/usr/bin/env python3
"""Build the oracle (oracle.c): a table of every function libstr.a defines
by its symbol (the post pass's <config>.<name> copies included), linked
with the library, static.

    oracle.py --libstr .build/libstr.a --out .build/proof/oracle
"""
import argparse
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--libstr', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cc', default=os.environ.get('CC', 'clang'))
    ap.add_argument('--nm', default=os.environ.get('NM', 'llvm-nm'))
    ap.add_argument('--dynamic', action='store_true', help='link the native oracle dynamically')
    args = ap.parse_args()
    out = subprocess.run([args.nm, '--defined-only', '-g', args.libstr], capture_output=True, text=True, check=True).stdout
    names = sorted({f[2] for f in (line.split() for line in out.splitlines()) if len(f) == 3 and f[1] == 'T'})
    table = ['#include <stddef.h>', 'struct oracle_fn { const char *name; void *fn; };']
    table += ['extern char f%d[] __asm__("%s");' % (i, n) for i, n in enumerate(names)]
    table += ['const struct oracle_fn oracle_fns[] = {'] + ['    {"%s", f%d},' % (n, i) for i, n in enumerate(names)] + ['    {0, 0},', '};']
    with tempfile.TemporaryDirectory() as d:
        gen = Path(d) / 'table.c'
        gen.write_text('\n'.join(table) + '\n')
        env = lambda k: shlex.split(os.environ.get(k, ''))
        subprocess.run([*shlex.split(args.cc), *env('CPPFLAGS'), *env('CFLAGS'), '-O1',
                        *([] if args.dynamic else ['-static']), HERE / 'oracle.c', gen,
                        args.libstr, *env('LDFLAGS'), '-o', args.out], check=True)


if __name__ == '__main__':
    main()
