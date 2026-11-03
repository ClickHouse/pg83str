#!/usr/bin/env python3
"""Post-compiler passes over the library objects, pure Python: decode, rewrite,
encode, write the objects and the archive.

    post.py [--out DIR] [--archive LIB.a] [--report FILE] [--roundtrip] [--check]
            [--passes a,b,c] [--func-align N] OBJECT.o|ARCHIVE.a ...

Each object is decoded with x86.py, rewritten by opt.py (nothing with
--roundtrip), laid out again by layout.py and written by elfout.py: once as
it is and once more, for every unit but dispatch, with the branches padded
off 32-byte boundaries (the Skylake JCC erratum) and its global functions
renamed name_jcc, as jcc-<object>; dispatch.c picks those on the hosts with
the erratum, since the padding costs about 5% elsewhere. --check compares
the result with the original: with --roundtrip the code bytes, relocations
and symbols must be identical; otherwise it reports the code sizes."""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import elf  # noqa: E402
import elfout  # noqa: E402
import layout  # noqa: E402
import opt  # noqa: E402
from objfile import Object  # noqa: E402
from units import UNITS, unit_of  # noqa: E402


def process(path, out, args, obj, code, trampolines):
    """The object rewritten, laid out without the JCC padding; and, for every unit
    but dispatch, a twin laid out with it, its global functions renamed name_jcc,
    for the hosts with the erratum (dispatch.c picks it there). Returns
    [(new path, report lines)]."""
    unit = unit_of(path)
    march = UNITS[unit]
    report = []
    if not args.roundtrip:
        for name in code:
            code[name], lines = opt.optimize(obj, code[name], evex='-march=x86-64-v4' in march, section=name, trampolines=trampolines)
            report += lines
    out_objects = []
    variants = [('', False, None)]
    if not args.roundtrip and unit != 'dispatch':
        exported = {s.name for s in obj.symbols if s.type == 'STT_FUNC' and s.bind == 'STB_GLOBAL' and s.section != 'UND'}
        variants.append(('jcc-', True, {name: name + '_jcc' for name in exported}))
    for prefix, jcc, rename in variants:
        jcc = jcc if not args.roundtrip else None
        layouts = {name: layout.layout(obj, name, ins, args.func_align, jcc_align=jcc) for name, ins in code.items()}
        remap = layout.resolve_relocs(obj, layouts)
        new = out / (prefix + Path(path).name)
        elfout.write_object(path, new, layouts, remap, rename)
        out_objects.append((new, report if not prefix else ['%s: with the JCC padding, %d functions as *_jcc' % (new.name, len(rename))]))
    return out_objects


def describe(path):
    """Code bytes, relocations and symbols of an object, for comparison."""
    e = elf.read(path)
    code = {s.name: s.data for s in e.sections if s.flags & 4 and s.type == 'SHT_PROGBITS'}
    relocs = {}
    for index, rs in e.relocs.items():
        name = e.sections[index].name
        if name == '.eh_frame':
            continue
        out = []
        for r in rs:
            sym = e.symbols[r.symbol]
            out.append((r.offset, r.type, e.sections[sym.shndx].name if sym.type == 'STT_SECTION' else sym.name, r.addend))
        relocs[name] = sorted(out)
    symbols = sorted((s.name, s.value, s.size, s.type, s.bind, e.section_name(s.shndx)) for s in e.symbols
                     if s.type != 'STT_SECTION' and not (0 < s.shndx < 0xff00 and e.sections[s.shndx].name in ('.eh_frame',)))
    return code, relocs, symbols


def compare(a, b):
    """First difference between two described objects, or None."""
    ca, ra, sa = a
    cb, rb, sb = b
    for name in ca:
        if ca[name] != cb.get(name):
            x, y = ca[name], cb.get(name, b'')
            k = next((k for k in range(min(len(x), len(y))) if x[k] != y[k]), min(len(x), len(y)))
            return 'code %s differs at %#x (%d vs %d bytes)' % (name, k, len(x), len(y))
    if ra != rb:
        for name in set(ra) | set(rb):
            if ra.get(name) != rb.get(name):
                d = [(x, y) for x, y in zip(ra.get(name, []), rb.get(name, [])) if x != y][:1]
                return 'relocations of %s differ: %s' % (name, d or (len(ra.get(name, [])), len(rb.get(name, []))))
    if sa != sb:
        d = [(x, y) for x, y in zip(sa, sb) if x != y][:1]
        return 'symbols differ: %s' % (d or (len(sa), len(sb)))
    return None


def inputs_of(paths, out):
    """Objects to process: .o files as given, members of .a archives extracted (our own ar reader)."""
    objects = []
    for p in paths:
        p = Path(p)
        if p.suffix == '.a':
            where = out / 'members' / p.stem
            where.mkdir(parents=True, exist_ok=True)
            for name, data in read_archive(p):
                if name.endswith('.o'):
                    (where / name).write_bytes(data)
                    objects.append(where / name)
        else:
            objects.append(p)
    return objects


def read_archive(path):
    data = path.read_bytes()
    assert data[:8] == b'!<arch>\n', path
    pos = 8
    long_names = b''
    members = []
    while pos + 60 <= len(data):
        name = data[pos:pos + 16].decode().rstrip()
        size = int(data[pos + 48:pos + 58])
        body = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size % 2)
        if name == '/':
            continue
        if name == '//':
            long_names = body
            continue
        if name.startswith('/'):
            start = int(name[1:])
            name = long_names[start:long_names.index(b'/\n', start)].decode()
        else:
            name = name.rstrip('/')
        members.append((name, body))
    return members


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', default='post', help='output directory')
    p.add_argument('--archive', help='also collect the new objects into this archive')
    p.add_argument('--report', help='write the pass report here (default: stdout)')
    p.add_argument('--roundtrip', action='store_true', help='no passes: decode, lay out, write')
    p.add_argument('--check', action='store_true', help='compare the result with the original')
    p.add_argument('--passes', help='comma-separated pass list (default: all), see opt.py')
    p.add_argument('--func-align', help='align every function to 2^N bytes (A/B runs)')
    p.add_argument('inputs', nargs='+')
    args = p.parse_args()
    if args.passes:
        os.environ['POST_PASSES'] = args.passes
    if args.roundtrip:
        os.environ['POST_JCC_ALIGN'] = '0'   # reproduce the object, do not improve its layout
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    failed = False
    objects = []
    # every object decoded first: a pass may look across them (the trampolines of dispatch.c)
    decoded = [(path, Object(path)) for path in inputs_of(args.inputs, out)]
    decoded = [(path, obj, obj.decode_all()) for path, obj in decoded]
    trampolines = {}
    for path, obj, code in decoded:
        trampolines.update(opt.trampolines_of(obj, code))
    for path, obj, code in decoded:
        unit = unit_of(path)
        for new, report in process(path, out, args, obj, code, trampolines):
            objects.append(new)
            lines += ['%s: %s' % (unit, line) for line in report]
            if args.check:
                diff = compare(describe(path), describe(new))
                if args.roundtrip:
                    lines.append('%s: %s' % (unit, 'identical' if diff is None else diff))
                    failed |= diff is not None
                else:
                    a, b = describe(path)[0], describe(new)[0]
                    lines.append('%s: %d -> %d bytes of code (%s)' % (unit, sum(map(len, a.values())), sum(map(len, b.values())), new.name))
    if args.archive:
        elfout.write_archive(args.archive, [(o.name, o.read_bytes()) for o in objects])
    text = '\n'.join(lines) + '\n'
    if args.report:
        Path(args.report).write_text(text)
    else:
        sys.stdout.write(text)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
