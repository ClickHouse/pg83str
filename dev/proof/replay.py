#!/usr/bin/env python3
"""The prover's machine against the hardware: random inputs run natively
(the oracle, ./build proof) and through machine.py with everything
concrete, the results and the memory after compared. What the proofs
rest on besides the specs is the instruction semantics; this checks them
on the paths the inputs take.

    python3 dev/proof/replay.py --func evex512.strlen_evex512 --spec strlen --n 2000
"""
import argparse
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import smt as S  # noqa: E402
from smt import const  # noqa: E402
import machine as X  # noqa: E402

PAGES = 2 * 4096


def offset(rng, room=0):
    """An offset in the two pages: near a page end or start often, anywhere else."""
    k = rng.random()
    if k < 0.4:
        return max(0, min(PAGES - 1 - room, 4096 - rng.randrange(1, 130)))
    if k < 0.6:
        return max(0, min(PAGES - 1 - room, PAGES - rng.randrange(1, 130) - room))
    return rng.randrange(0, max(1, PAGES - room))


def length(rng, most):
    k = rng.random()
    n = rng.randrange(0, 70) if k < 0.5 else rng.randrange(0, 600) if k < 0.85 else rng.randrange(0, most + 1)
    return min(n, most)


def nonzero(rng):
    return rng.randrange(1, 256)


def gen_strlen(rng):
    buf = bytearray(rng.randrange(0, 256) for _ in range(PAGES))
    s = offset(rng)
    n = length(rng, PAGES - 1 - s)
    for i in range(n):
        buf[s + i] = nonzero(rng)
    buf[s + n] = 0
    return ['p%d' % s], buf


GENS = {'strlen': gen_strlen}


def emulate(img, mach, func, args, base, buf):
    regs = []
    for a in args:
        regs.append(const(64, base + int(a[1:]) if a[0] == 'p' else int(a[1:], 0)))
    st = X.fresh_state(img.symbols[func], regs)
    for r in st.gpr:
        if r not in ('rsp',) and not any(r == x for x in ('rdi', 'rsi', 'rdx', 'rcx', 'r8', 'r9')[:len(regs)]):
            st.gpr[r] = const(64, 0x5a5a5a5a5a5a5a5a)
    st.cmem = (base, bytearray(buf))
    while not st.done:
        if st.steps > 200000:
            raise X.Unsupported('no return after %d instructions' % st.steps)
        succ = mach.step(st)
        live = [s for s, c in succ if c is not S.FALSE]
        if len(live) != 1:
            raise X.Unsupported('a branch on an unknown value at %#x' % st.rip)
        st = live[0]
    return st.gpr['rax'], st.cmem[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', default=str(ROOT / '.build/proof/libstr.elf'))
    ap.add_argument('--oracle', default=str(ROOT / '.build/proof/oracle'))
    ap.add_argument('--func', required=True, help='comma list')
    ap.add_argument('--spec', required=True, choices=sorted(GENS))
    ap.add_argument('--n', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=1)
    args = ap.parse_args()
    img = X.Image(args.elf)
    o = subprocess.Popen([args.oracle], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    base = int(o.stdout.readline().split()[1], 16)
    rng = random.Random(args.seed)
    bad = 0
    for func in args.func.split(','):
        mach = X.Machine(img, bmi=not func.startswith(('sse2.', 'sse42.')))
        for k in range(args.n):
            fargs, buf = GENS[args.spec](rng)
            o.stdin.write('%s %s x%s\n' % (func, ' '.join(fargs), buf.hex()))
            o.stdin.flush()
            r, after = o.stdout.readline().split()
            r, after = int(r, 16), bytes.fromhex(after)
            er, eafter = emulate(img, mach, func, fargs, base, buf)
            if not S.is_const(er) or er.val != r or eafter != after:
                bad += 1
                print('MISMATCH %s %s: native %#x, machine %r%s' % (func, ' '.join(fargs), r, er, '' if eafter == after else ', memory differs'))
                if bad > 10:
                    sys.exit(1)
        print('%s: %d inputs, the machine agrees with the hardware' % (func, args.n), flush=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
