#!/usr/bin/env python3
"""Compare repeated mixed runs, including pg83's min-minus-noop metric.

Every input must contain the same tables, a glibc reference, and noop.
Nonpositive net times are rejected rather than clamped to a fabricated floor.
The gross medians and median-minus-median-noop figures remain alongside the
upstream metric so the subtraction and choice of statistic stay explicit.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics


def read_run(path, reference):
    tables = {}
    for r in csv.DictReader(path.open()):
        if r['prim'] == 'lib' and r['scenario'] == 'trace:mixed':
            if r['kernel'] in tables:
                raise ValueError('duplicate mixed table in ' + str(path))
            tables[r['kernel']] = {'gross_min_ns': float(r['min_ns']), 'gross_median_ns': float(r['med_ns'])}
    noop = tables.pop('noop')
    for values in tables.values():
        values['net_min_ns'] = values['gross_min_ns'] - noop['gross_min_ns']
        values['net_median_ns'] = values['gross_median_ns'] - noop['gross_median_ns']
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ValueError('nonpositive or nonfinite time in ' + str(path))
    ref = tables[reference]
    for values in tables.values():
        for metric in ('gross_median', 'net_min', 'net_median'):
            values[metric + '_ratio'] = values[metric + '_ns'] / ref[metric + '_ns']
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'noop': noop, 'tables': tables}


def compare(paths, reference):
    runs = [read_run(path, reference) for path in paths]
    names = set(runs[0]['tables'])
    if any(set(run['tables']) != names for run in runs):
        raise ValueError('mixed table sets differ between runs')
    summary = {}
    for name in sorted(names):
        values = [run['tables'][name] for run in runs]
        entry = {}
        for metric in ('gross_median', 'net_min', 'net_median'):
            ratios = [v[metric + '_ratio'] for v in values]
            entry[metric + '_ratio'] = {'median': statistics.median(ratios), 'min': min(ratios),
                                       'max': max(ratios), 'below_0_70': sum(v < 0.70 for v in ratios)}
        entry['upstream_best_across_runs_ratio'] = min(v['net_min_ns'] for v in values) / min(
            run['tables'][reference]['net_min_ns'] for run in runs)
        summary[name] = entry
    return {'reference': reference, 'definition': 'net_min = table min_ns - same-run noop min_ns',
            'runs': runs, 'summary': summary}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv', type=Path, nargs='+')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--reference', default='glibc/dispatch')
    p.add_argument('--target', default='ours/target_split')
    p.add_argument('--require-ratio', type=float, help='fail if any paired net-min target ratio exceeds this value')
    a = p.parse_args()
    result = compare(a.csv, a.reference)
    selected = result['summary'][a.target]
    a.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'target': a.target, 'runs': len(result['runs']), **selected}, indent=2))
    if a.require_ratio is not None and selected['net_min_ratio']['max'] > a.require_ratio:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
