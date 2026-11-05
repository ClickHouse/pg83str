#!/usr/bin/env python3
"""The term layer's simplifications against z3: random expressions built
twice, through smt.py's folding constructors and as raw nodes with nothing
folded, then z3 asked for an input on which the two differ. A proof is only
as sound as these rewrites; any difference found is a bug in them.

    ix run bin/z3 -- python3 dev/proof/check_smt.py [--n 3000] [--seed 1]

A query z3 can not settle in 20 s (two 64-bit products told apart by bit
blasting: distributivity is hard for it) counts as undecided, not as a
difference.
"""
import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smt as S  # noqa: E402
from smt import const, var  # noqa: E402

RAW = {'add', 'sub', 'mul', 'and', 'or', 'xor', 'shl', 'lshr', 'ashr', 'neg', 'not', 'extract', 'concat', 'zext', 'sext', 'ite', 'eq', 'ult',
       'ule', 'slt', 'sle'}


def raw(op, w, *args):
    return S._mk(op, w, tuple(args))


def gen(rng, w, depth, leaves):
    """(folded, raw) of a random w-bit expression."""
    if depth == 0 or rng.random() < 0.25:
        k = rng.random()
        if k < 0.35 and w in leaves:
            v = rng.choice(leaves[w])
            return v, v
        c = rng.choice([0, 1, 2, w - 1, 15, 16, 31, 32, 63, 64, 0x1f, 0x3f, 0xfff, 0xfc0, 0xffffffffffffffe0, rng.getrandbits(64)])
        return const(w, c), const(w, c)
    op = rng.choice(['add', 'sub', 'mul', 'and', 'or', 'xor', 'shl', 'lshr', 'neg', 'not', 'extract', 'concat', 'zext', 'ite', 'and_mask'])
    if op in ('add', 'sub', 'mul', 'and', 'or', 'xor', 'shl', 'lshr'):
        a, ra = gen(rng, w, depth - 1, leaves)
        b, rb = gen(rng, w, depth - 1, leaves)
        if op in ('shl', 'lshr'):
            b, rb = const(w, rng.randrange(w + 2)), None
            rb = b
        f = {'add': S.add, 'sub': S.sub, 'mul': S.mul, 'and': S.band, 'or': S.bor, 'xor': S.bxor, 'shl': S.shl, 'lshr': S.lshr}[op]
        return f(a, b), raw(op, w, ra, rb)
    if op == 'and_mask':  # the rounding the code does: and with -2^j or 2^j - 1
        a, ra = gen(rng, w, depth - 1, leaves)
        j = rng.randrange(1, min(w, 13))
        m = const(w, -(1 << j) if rng.random() < 0.5 else (1 << j) - 1)
        return S.band(a, m), raw('and', w, ra, m)
    if op in ('neg', 'not'):
        a, ra = gen(rng, w, depth - 1, leaves)
        return (S.neg if op == 'neg' else S.bnot)(a), raw(op, w, ra)
    if op == 'extract' and w < 64:
        src = rng.choice([x for x in (8, 32, 64) if x >= w])
        a, ra = gen(rng, src, depth - 1, leaves)
        lo = rng.randrange(src - w + 1)
        return S.extract(a, lo + w - 1, lo), raw('extract', w, ra, lo + w - 1, lo)
    if op == 'concat' and w >= 16:
        w1 = rng.choice([x for x in (8, 32) if x < w] or [w // 2])
        a, ra = gen(rng, w - w1, depth - 1, leaves)
        b, rb = gen(rng, w1, depth - 1, leaves)
        return S.concat(a, b), raw('concat', w, ra, rb)
    if op == 'zext' and w > 8:
        src = rng.choice([x for x in (8, 32) if x < w])
        a, ra = gen(rng, src, depth - 1, leaves)
        return S.zext(a, w), raw('zext', w, ra)
    if op == 'ite':
        a, ra = gen(rng, w, depth - 1, leaves)
        b, rb = gen(rng, w, depth - 1, leaves)
        x, rx = gen(rng, w, depth - 1, leaves)
        y, ry = gen(rng, w, depth - 1, leaves)
        cmp = rng.choice([(S.ult, 'ult'), (S.eq, 'eq'), (S.ule, 'ule')])
        return S.ite(cmp[0](x, y), a, b), raw('ite', w, raw(cmp[1], S.BOOL, rx, ry), ra, rb)
    return gen(rng, w, depth - 1, leaves)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n', type=int, default=3000)
    ap.add_argument('--seed', type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    base = S.concat(var('b_hi', 58), const(6, 0))  # an aligned base, as the proofs make pointers
    leaves = {64: [var('x', 64), var('y', 64), base, S.add(base, const(64, 37)), S.add(base, var('k', 64))], 32: [var('u', 32)], 8: [var('c', 8)]}
    os.environ.setdefault('PROVE_TIMEOUT', '20')
    S.TIMEOUT = int(os.environ['PROVE_TIMEOUT'])
    solver = S.Solver()
    bad = undecided = 0
    for k in range(args.n):
        w = rng.choice([64, 64, 32, 8])
        f, r = gen(rng, w, 4, leaves)
        if f is r:
            continue
        res = solver.check(S.not_(S._mk('eq', S.BOOL, (f, r))), keep=True)
        if res == 'sat':
            bad += 1
            print('DIFFERENT (case %d, %d bits): %s' % (k, w, ' '.join(solver.values([f, r]).split())))
            if bad > 5:
                break
        elif res == 'unknown':
            undecided += 1
    print('%d random expressions: %s, %d undecided' % (args.n, 'the folding agrees with z3' if not bad else '%d differ' % bad, undecided))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
