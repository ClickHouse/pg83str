#!/usr/bin/env python3
"""The state of ./build proof: each function's node (dev/proof/targets.py)
by its output under .build/proof/, proved how (one class, a split, another
copy's proof) and in how long; then what is not proved yet. The build puts
there the outputs of the nodes it was asked for (./build proof: every
function's; a dependency's stays in its cache).

    dev/proof/report.py [--build .build]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import targets  # noqa: E402


def result_of(path):
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith('RESULT '):
            return json.loads(line[7:])
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', default=str(HERE.parent.parent / '.build'))
    args = ap.parse_args()
    out = Path(args.build) / 'proof'
    done, missing = [], []
    for spec in targets.FUNCS:
        for config in targets.CONFIGS:
            name = targets.name(spec, config)
            r = result_of(out / (name + '.txt'))
            if r and r.get('result') == 'proved':
                how = ('as %s' % r['same_as']) if r.get('same_as') else ('%d classes' % r['classes']) if r.get('classes', 1) > 1 else 'one class'
                done.append('%-22s %-28s %-24s %6s paths %9.0f s' % (name, targets.FUNCS[spec][config], how, r.get('paths', ''),
                                                                         r.get('seconds', 0)))
            else:
                split = targets.SPLIT.get((spec, config))
                missing.append('%-22s %-28s %s' % (name, targets.FUNCS[spec][config], '%d classes' % split if split else 'one class'))
    print('proved (%d of %d):' % (len(done), len(done) + len(missing)))
    print('\n'.join('  ' + x for x in done))
    if missing:
        print('not proved (%d):' % len(missing))
        print('\n'.join('  ' + x for x in missing))


if __name__ == '__main__':
    main()
