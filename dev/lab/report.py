#!/usr/bin/env python3
"""Summarize a lab run: where each kernel stands, where ours lose and to whom.

    dev/lab/report.py TAG [--hosts local,lab2,lab3] [--prims list] [--regime thr|lat] [--stat med|min] [--loss 1.10] [--csv FILE]

Reads .lab/<tag>/<host>/{out.csv.gz,meta.json}. "ours" is the configuration the
host's dispatch installs (meta.json), "glibc" the variant glibc's IFUNC picks
there, "best" the fastest valid kernel of the primitive. Per host:
  traces    ns per call on each workload trace: ours, glibc, the best five;
  buckets   per primitive, scenario and size bucket, the geometric mean over
            the bucket's sizes and alignments of each kernel's minimum; the
            winner, ours/best and glibc/best (* where ours lose by --loss);
  losses    the cells (size, alignment) where ours/best is worst, and who wins.
The statistic is the median over a cell's rounds by default: the rounds are
interleaved, and on a host whose clock moves (a laptop's boost) the minimum
catches the lucky batch, not the code. --csv writes the bucket table of every
host for other tools.
"""
import argparse
import collections
import csv
import gzip
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BUCKETS = [(0, 0), (1, 8), (9, 16), (17, 32), (33, 64), (65, 128), (129, 256), (257, 512), (513, 1024), (1025, 4096), (4097, 65536), (65537, 1 << 40)]


def bucket(n):
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return '%d' % lo if lo == hi else '%d-%s' % (lo, hi if hi < 1 << 40 else '')
    raise ValueError(n)


def geomean(xs):
    return math.exp(sum(math.log(max(x, 1e-3)) for x in xs) / len(xs))


def short(name, width=44):
    return name if len(name) <= width else name[:width - 1] + '~'


def load(path, regime, prims, stat):
    """cells[(prim, scenario, n, align)][kernel] = min_ns; traces[(prim, trace)][kernel] = min_ns."""
    cells, traces = collections.defaultdict(dict), collections.defaultdict(dict)
    with gzip.open(path, 'rt') as f:
        for r in csv.DictReader(f):
            if prims and r['prim'] not in prims:
                continue
            if r['regime'] == 'trace':
                traces[(r['prim'], r['scenario'][6:])][r['kernel']] = float(r[stat + '_ns'])
            elif r['regime'] == regime:
                cells[(r['prim'], r['scenario'], int(r['n']), r['align'])][r['kernel']] = float(r[stat + '_ns'])
    return cells, traces


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tag')
    ap.add_argument('--hosts', default='local,lab2,lab3')
    ap.add_argument('--prims')
    ap.add_argument('--regime', default='thr')
    ap.add_argument('--stat', default='med', choices=('med', 'min'))
    ap.add_argument('--loss', type=float, default=1.10)
    ap.add_argument('--worst', type=int, default=12, help='cells listed per primitive in the losses')
    ap.add_argument('--csv', type=Path)
    args = ap.parse_args()
    prims = set(args.prims.split(',')) if args.prims else None
    rows = []
    for host in args.hosts.split(','):
        d = ROOT / '.lab' / args.tag / host
        if not (d / 'out.csv.gz').exists():
            print('== %s: no results' % host)
            continue
        meta = json.loads((d / 'meta.json').read_text())
        cells, traces = load(d / 'out.csv.gz', args.regime, prims, args.stat)
        ours_cfg = 'ours/' + meta['ours_config']
        # memcpy and memmove are one function in glibc: the variant comes back under either name
        glibc_of = {p: 'glibc/' + v.replace('__memcpy_', '__%s_' % p) if p in ('memcpy', 'memmove') else 'glibc/' + v for p, v in meta['glibc'].items()}
        print('== %s: %s; ours %s; regime %s, %s' % (host, meta['brand'].strip(), meta['ours_config'], args.regime, args.stat))
        bad = [k for k in meta['kernels'] if k['status'] == -1]
        if bad:
            print('   invalid: ' + ', '.join('%s (%s)' % (k['name'], k['why']) for k in bad))

        print('\n-- traces, ns per call: ours, glibc, the best five')
        for (prim, name), ks in sorted(traces.items()):
            best = sorted(ks.items(), key=lambda kv: kv[1])
            o, g, b = ks.get(ours_cfg), ks.get(glibc_of[prim]), best[0][1]
            print('%-8s %-13s ours %6.2f (%.2f)  glibc %6.2f (%.2f)  | %s' % (
                prim, name, o, o / b, g, g / b, '  '.join('%s %.2f' % (short(k, 40), v) for k, v in best[:5])))

        print('\n-- buckets: geometric mean over sizes and alignments; winner, ours/best, glibc/best')
        groups = collections.defaultdict(lambda: collections.defaultdict(list))
        for (prim, sc, n, al), ks in cells.items():
            for k, v in ks.items():
                groups[(prim, sc, bucket(n))][k].append(v)
        order = {b: i for i, b in enumerate(bucket(lo) for lo, _ in BUCKETS)}
        last = None
        for (prim, sc, b), ks in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], order[kv[0][2]])):
            full = max(len(v) for v in ks.values())
            gm = {k: geomean(v) for k, v in ks.items() if len(v) == full}
            win = min(gm, key=gm.get)
            o, g = gm.get(ours_cfg), gm.get(glibc_of[prim])
            if (prim, sc) != last:
                print('%s %s' % (prim, sc))
                last = (prim, sc)
            ro, rg = (o / gm[win] if o else float('nan')), (g / gm[win] if g else float('nan'))
            print('   %-11s best %7.2f %-46s ours %5.2f%s glibc %5.2f' % (b, gm[win], short(win, 46), ro, '*' if ro >= args.loss else ' ', rg))
            rows.append(dict(host=host, prim=prim, scenario=sc, bucket=b, best=win, best_ns=round(gm[win], 4), ours=round(o or 0, 4),
                             glibc=round(g or 0, 4), ours_vs_best=round(ro, 4), glibc_vs_best=round(rg, 4)))

        print('\n-- losses: the worst cells of ours against the best kernel of the cell')
        per = collections.defaultdict(list)
        for (prim, sc, n, al), ks in cells.items():
            if ours_cfg not in ks:
                continue
            win = min(ks, key=ks.get)
            per[prim].append((ks[ours_cfg] / ks[win], sc, n, al, win, ks[win], ks[ours_cfg]))
        for prim in sorted(per):
            worst = sorted(per[prim], reverse=True)[:args.worst]
            print('%s: %s' % (prim, '; '.join('%s n=%d @%s %.2f (%s %.1f vs %.1f)' % (sc, n, al, r, short(w, 36), wv, ov)
                                            for r, sc, n, al, w, wv, ov in worst if r >= args.loss) or 'none over %.2f' % args.loss))
        print()
    if args.csv and rows:
        with open(args.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == '__main__':
    main()
