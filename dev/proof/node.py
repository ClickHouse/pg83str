#!/usr/bin/env python3
"""One node of ./build proof (dev/proof/targets.py), run where the build runs
it (here, or a --dist host): a function's proof in one class or one of its
classes, the sum of a function's classes, or the proof of a copy whose code
is another's.

    node.py --elf E --spec S --func F --out O [--classes N --align K] [--callee O ...]
    node.py --elf E --spec S --func F --out O --of O1 O2 ...       the classes' sum
    node.py --elf E --spec S --func F --out O --same-as F2 --of O2  another copy's proof

--callee: the outputs of the proofs whose contracts the spec takes for the
function's callees (strcat): each callee must be one of them, proved. The
solvers: --bin, a directory with z3 and bitwuzla (the proof_solvers node's).
STR_PROOF_WORKERS, STR_PROOF_TIMEOUT, STR_PROOF_MEMORY (build.py sets them
from -D) go to prove.py. The output: the RESULT line and how the proof
ended; the node fails unless proved."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


def result_of(path):
    """The RESULT line of a node's output, as a dict; None without one."""
    for line in Path(path).read_text().splitlines():
        if line.startswith('RESULT '):
            return json.loads(line[7:])
    return None


def write(out, r, text=''):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text + 'RESULT ' + json.dumps(r) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', required=True)
    ap.add_argument('--spec', required=True)
    ap.add_argument('--func', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--bin', help='the directory with z3 and bitwuzla')
    ap.add_argument('--classes', type=int)
    ap.add_argument('--align', type=int, default=0)
    ap.add_argument('--of', nargs='+', default=[], help="the classes' outputs, or the other copy's")
    ap.add_argument('--same-as', help='the copy whose code this one has')
    ap.add_argument('--callee', nargs='+', default=[], help='the outputs of the proofs of the callees the spec takes by contract')
    args = ap.parse_args()
    import machine as X
    import prove
    import run
    img = X.Image(args.elf)

    if args.same_as:  # the same code, checked the way run.py groups copies (sse2 and sse42 decode tzcnt as bsf)
        bmi = lambda f: not f.startswith(('sse2.', 'sse42.'))  # noqa: E731
        if bmi(args.func) != bmi(args.same_as) or \
                run.fingerprint(img, img.symbols[args.func]) != run.fingerprint(img, img.symbols[args.same_as]):
            sys.exit('%s: its code is not %s\'s any more: targets.SAME is stale' % (args.func, args.same_as))
        r = result_of(args.of[0])
        if not r or r.get('result') != 'proved':
            sys.exit('%s: %s is not proved' % (args.func, args.same_as))
        write(args.out, {**r, 'func': args.func, 'same_as': args.same_as}, 'the code of %s, proved with it\n' % args.same_as)
        return

    if args.of:  # the classes: a case split, all of them every pointer
        rs = [result_of(o) for o in args.of]
        bad = [o for o, r in zip(args.of, rs) if not r or r.get('result') != 'proved']
        if bad:
            sys.exit('%s: classes not proved: %s' % (args.func, ', '.join(bad)))
        write(args.out, {'func': args.func, 'spec': args.spec, 'classes': len(rs), 'result': 'proved',
                         'paths': sum(r['paths'] for r in rs), 'obligations': sum(r['obligations'] for r in rs),
                         'seconds': round(sum(r['seconds'] for r in rs), 1)})
        return

    if args.callee:  # every callee taken by its contract proved on its own
        proved = {}
        for o in args.callee:
            r = result_of(o)
            if r and r.get('result') == 'proved':
                proved[r['func']] = r
        names = {a: n for n, a in img.symbols.items()}
        missing = [names.get(t, hex(t)) for t in prove.contract_targets(img, img.symbols[args.func]) if names.get(t) not in proved]
        if missing:
            sys.exit('%s: callees without a proof among the dependencies: %s' % (args.func, ', '.join(missing)))

    env = dict(os.environ, PROVE_TIMEOUT=os.environ.get('STR_PROOF_TIMEOUT', '1800'), PROVE_MEMORY=os.environ.get('STR_PROOF_MEMORY', '3000'))
    if args.bin:
        env['PATH'] = str(Path(args.bin).resolve()) + os.pathsep + env.get('PATH', '')
        if (Path(args.bin) / 'bitwuzla').exists():
            env['PROVE_BITWUZLA'] = str((Path(args.bin) / 'bitwuzla').resolve())
    cmd = [sys.executable, str(HERE / 'prove.py'), '--elf', args.elf, '--func', args.func, '--spec', args.spec, '--induct',
           '--classes', str(args.classes or 1), '--align', str(args.align), '--workers', os.environ.get('STR_PROOF_WORKERS', '1')]
    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = p.stdout.splitlines()
    r = next((json.loads(x[7:]) for x in lines if x.startswith('RESULT ')), None)
    tail = '\n'.join(x for x in lines[-20:] if not x.startswith('RESULT ')) + '\n'
    if r is None:
        r = {'func': args.func, 'spec': args.spec, 'classes': args.classes or 1, 'align': args.align, 'result': 'error',
             'detail': (lines[-1] if lines else 'rc=%d' % p.returncode)}
    write(args.out, r, tail)
    print('%s %s class %s/%s: %s' % (args.spec, args.func, args.align, args.classes or 1, r['result']), flush=True)
    if r['result'] != 'proved':
        sys.exit(1)


if __name__ == '__main__':
    main()
