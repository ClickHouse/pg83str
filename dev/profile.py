#!/usr/bin/env python3
"""Turn perf samples of a linked binary into the profile the order pass reads.

    profile.py --binary BENCH --map LAYOUT-MAP.json [--merge-into dev/profile.json] IPS...

IPS: files of instruction addresses, one per line, as `perf script -F ip`
prints them. The binary's function symbols place each address in a function
and at an offset of its final layout; the layout map (post.py --layout-map)
takes that offset back to the instruction it came from in the compiler's
object, so the counts are about the instructions the passes see. A copy of a
function made for a configuration (<configuration>.<function>) and the
Skylake twin (<function>_jcc) count for the function. The result,
{function: {original offset: samples}}, is merged into --merge-into by
adding counts: profiles of several hosts add up."""
import argparse
import bisect
import json
import subprocess
from collections import defaultdict
from pathlib import Path


def symbols(binary, nm):
    out = []
    for line in subprocess.check_output([nm, '-S', '--defined-only', binary], text=True).splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[2] in ('T', 't'):
            out.append((int(parts[0], 16), int(parts[1], 16), parts[3]))
    out.sort()
    return out


def locate(name, maps):
    """(object, section) of a function symbol in the layout map, or None."""
    if name.endswith('_jcc'):
        section, objects = '.text.' + name[:-4], [o for o in maps if o.startswith('jcc-')]
    elif '.' in name:
        section, objects = '.text.' + name, [o for o in maps if not o.startswith('jcc-')]
    else:
        section, objects = '.text.' + name, [o for o in maps if not o.startswith('jcc-')]
    for o in objects:
        if section in maps[o]:
            return o, section
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--binary', required=True)
    p.add_argument('--map', required=True)
    p.add_argument('--nm', default='nm')
    p.add_argument('--merge-into', default=str(Path(__file__).resolve().parent / 'profile.json'))
    p.add_argument('ips', nargs='+')
    a = p.parse_args()
    maps = json.load(open(a.map))
    syms = symbols(a.binary, a.nm)
    starts = [s[0] for s in syms]
    back = {}   # (object, section) -> (sorted new offsets, {new: old})
    for o, sections in maps.items():
        for section, m in sections.items():
            news = sorted(int(k) for k in m)
            back[(o, section)] = (news, {int(k): v for k, v in m.items()})
    counts = defaultdict(lambda: defaultdict(int))
    total = placed = 0
    for path in a.ips:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            ip = int(line, 16)
            total += 1
            k = bisect.bisect_right(starts, ip) - 1
            if k < 0:
                continue
            addr, size, name = syms[k]
            if ip >= addr + size:
                continue
            where = locate(name, maps)
            if where is None:
                continue
            news, m = back[where]
            j = bisect.bisect_right(news, ip - addr) - 1
            if j < 0:
                continue
            fname = name[:-4] if name.endswith('_jcc') else name.split('.', 1)[1] if '.' in name else name
            counts[fname][str(m[news[j]])] += 1
            placed += 1
    merged = {}
    out = Path(a.merge_into)
    if out.exists():
        merged = json.load(open(out))
    for fname, offsets in counts.items():
        slot = merged.setdefault(fname, {})
        for off, n in offsets.items():
            slot[off] = slot.get(off, 0) + n
    out.write_text(json.dumps(merged, indent=0, sort_keys=True) + '\n')
    print('%d samples, %d in the library (%d functions); %s now has %d functions' % (total, placed, len(counts), out, len(merged)))


if __name__ == '__main__':
    main()
