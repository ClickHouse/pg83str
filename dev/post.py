#!/usr/bin/env python3
"""Post-compiler passes over the library objects.

    post.py [--cc CC] [--out DIR] [--archive LIB.a] [--report FILE]
            [--roundtrip] [--check] [--passes a,b,c] [--align] [--func-align N]
            OBJECT.o|ARCHIVE.a ...

Each object is decoded with x86.py, rewritten by opt.py (nothing with
--roundtrip), emitted as GAS by emit.py and assembled again with CC and the
-march of its unit (dev/units.py). --check compares the instruction sequence
before and after; with --roundtrip it must be identical. --archive collects
the new objects into one archive."""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import emit  # noqa: E402
import opt  # noqa: E402
from objfile import Object  # noqa: E402
from units import UNITS, unit_of  # noqa: E402


def shapes(path):
    """Instruction texts with addresses abstracted, nops skipped, for every code section."""
    obj = Object(path)
    out = []
    for name in obj.code_sections():
        for i in obj.decode(name):
            if i.mnemonic == 'nop':
                continue
            out.append(re.sub(r'0x[0-9A-F]{16}', 'L', i.gas))
    return out


def process(path, out, args):
    unit = unit_of(path)
    march = UNITS[unit]
    obj = Object(path)
    code = obj.decode_all()
    report = []
    if not args.roundtrip:
        for name in code:
            code[name], lines = opt.optimize(obj, code[name], evex='-march=x86-64-v4' in march, section=name)
            report += lines
    asm = out / (Path(path).stem + '.s')
    emit.emit(obj, code, asm)
    new = out / Path(path).name
    align = ['-mbranches-within-32B-boundaries'] if args.align and not args.roundtrip else []
    subprocess.run([*args.cc, *march, *align, '-c', str(asm), '-o', str(new)], check=True)
    return new, report


def inputs_of(paths, out):
    """Objects to process: .o files as given, members of .a archives extracted."""
    objects = []
    for p in paths:
        p = Path(p)
        if p.suffix == '.a':
            where = out / 'members' / p.stem
            where.mkdir(parents=True, exist_ok=True)
            subprocess.run([ar(), 'x', str(p.resolve())], cwd=where, check=True)
            objects += sorted(where.glob('*.o'))
        else:
            objects.append(p)
    return objects


AR = None


def ar():
    return AR or os.environ.get('AR') or shutil.which('ar') or shutil.which('llvm-ar') or 'ar'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--cc', default=os.environ.get('CC', 'cc'), help='compiler used to assemble (default: $CC or cc)')
    p.add_argument('--ar', help='archiver (default: $AR, ar or llvm-ar)')
    p.add_argument('--out', default='post', help='output directory')
    p.add_argument('--archive', help='also collect the new objects into this archive')
    p.add_argument('--report', help='write the pass report here (default: stdout)')
    p.add_argument('--roundtrip', action='store_true', help='no passes: decode, emit, assemble')
    p.add_argument('--check', action='store_true', help='compare the instruction sequence before and after')
    p.add_argument('--passes', help='comma-separated pass list (default: all), see opt.py')
    p.add_argument('--align', action='store_true', help='assemble with -mbranches-within-32B-boundaries')
    p.add_argument('--func-align', help='align every function to 2^N bytes (A/B runs)')
    p.add_argument('inputs', nargs='+')
    args = p.parse_args()
    global AR
    AR = args.ar
    args.cc = [*shlex.split(args.cc), *shlex.split(os.environ.get('CPPFLAGS', '')), *shlex.split(os.environ.get('CFLAGS', ''))]
    if args.passes:
        os.environ['POST_PASSES'] = args.passes
    if args.func_align:
        os.environ['POST_FUNC_ALIGN'] = args.func_align
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    failed = False
    objects = []
    for path in inputs_of(args.inputs, out):
        new, report = process(path, out, args)
        objects.append(new)
        unit = unit_of(path)
        lines += ['%s: %s' % (unit, line) for line in report]
        if args.check:
            a, b = shapes(path), shapes(new)
            same = a == b
            lines.append('%s: %d -> %d instructions, %s' % (unit, len(a), len(b), 'identical' if same else 'differs'))
            if args.roundtrip and not same:
                for k, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        lines.append('  first difference at %d: %r vs %r' % (k, x, y))
                        break
                failed = True
    if args.archive:
        archive = Path(args.archive)
        if archive.exists():
            archive.unlink()
        subprocess.run([ar(), 'rcs', str(archive), *map(str, objects)], check=True)
    text = '\n'.join(lines) + '\n'
    if args.report:
        Path(args.report).write_text(text)
    else:
        sys.stdout.write(text)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
