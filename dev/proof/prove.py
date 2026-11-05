#!/usr/bin/env python3
"""Proofs of the library's functions on their machine code.

    ix run bin/z3 -- python3 dev/proof/prove.py --elf .build/proof/libstr.elf --func evex512.strlen_evex512 --spec strlen --induct

The linked library (./build proof) is executed symbolically from the
function's entry: a pointer is its low bits (one alignment class per run,
--align, 64 in all) and an unknown ALIGN-aligned base, so every place in a
page at once; the memory is an array of unknown bytes; every other register
is unknown. Every feasible path is followed (z3 decides each branch both
ways), and at the return the spec's obligations are proved for every input
the path admits: no input exists that breaks one.

Two modes for the loops:
  --max N    bounded: inputs up to N bytes, the loops unrolled as far as
             they go (time grows with the square of N);
  --induct   unbounded: at a loop's head, the second time a path reaches
             it, the registers that changed by one constant step become
             their first value plus K, any multiple of the step, and the rest
             of the state unknown. The spec's invariant is proved at the
             first arrival (the base) and one iteration from the general
             state either leaves with the obligations met or comes back to
             the head in the same general form with K + step, the invariant
             kept (the step). The lengths are then unbounded.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import smt as S  # noqa: E402
from smt import const, var  # noqa: E402
import machine as X  # noqa: E402

USER_LO, USER_HI = 0x10000, (1 << 47) - (1 << 32)  # canonical user addresses, with room for any object
ALIGN = 64  # the proof splits on a pointer's low bits: at least the widest vector's alignment, more when the code aligns further (align_of)
K_LIMIT = 1 << 40  # a loop's distance from its first iteration: below this, no address wraps


def page(a):
    return S.lshr(a, const(64, 12))


class Failure(Exception):
    pass


class Restart(Exception):
    """A loop's invariant candidate failed its step: prove the class again
    with the next, weaker one for that loop."""

    def __init__(self, head):
        self.head = head


def align_of(img, entry):
    """The classes a function's proof splits a pointer into: 64, or the
    largest alignment its code rounds an address down to (an and with -2^j),
    so that every such address stays the base plus a known offset."""
    best, seen, todo = ALIGN, set(), [entry]
    while todo:
        a = todo.pop()
        while a not in seen:
            seen.add(a)
            j = img.decode(a)
            mn = j['mnemonic']
            if mn == 'int3':
                break
            if mn == 'and':
                for o in j['ops']:
                    if o['kind'] == 'Immediate':
                        v = S.signed(S.sext(const(o['size'], o['imm']), 64)) if o.get('signed') else o['imm']
                        if v < 0 and (-v) & (-v - 1) == 0 and -v <= 4096:
                            best = max(best, -v)
            if j.get('branch_target') is not None:
                todo.append(j['branch_target'])
            if mn in ('ret', 'jmp'):
                break
            a += j['len']
    return best


def aligned_pointer(name, a, align):
    """A pointer with its low bits a and the rest unknown: base + a, the base
    known to be align-aligned, and the memory around it an array of its own."""
    sh = align.bit_length() - 1
    base = S.concat(var(name + '_hi', 64 - sh), const(sh, 0))
    return base, S.add(base, const(64, a)), S.mem_var(name + '_mem')


class Scan:
    """A scan from s for the first byte that matches: strlen (a zero byte,
    unbounded) and memchr (c within n bytes, as C11 has it: read as if one
    byte at a time, stopping at the match, so the object may end there).
    The answer's offset r; the last byte that must be readable: the match,
    or for memchr without one the n-th. Every load lies in the pages of
    [s, s + last]; no stores; aligned loads aligned.
    A loop's invariant at its frontier F (a register pointing into the
    object): no match in [s, F), the frontier within the object, and every
    load so far either in the pages up to F - 1's (B) or ending by F + step
    with F aligned to the step (A: [F, F + step) is then F's page, and F's
    byte must be in the object, which the return checks)."""
    bounded = False

    def __init__(self, maxlen, induct):
        self.maxlen, self.induct = maxlen, induct

    def setup(self, a, align):
        self.base, self.s, arr = aligned_pointer('s', a, align)
        self.regions = [(self.base, arr)]
        self.z = var('z', 64)  # where the scan must stop at the latest: a match, or n
        self.i = var('i', 64)
        params = self.params()
        stop = self.match(self.byte(S.add(self.s, self.z)))
        if self.bounded:
            stop = S.or_(S.and_(S.ult(self.z, self.n), stop), S.eq(self.z, self.n))
        # the object, up to where the scan must stop, inside user space
        assume = [S.ule(const(64, USER_LO), self.s), S.ult(self.s, const(64, USER_HI)), stop, S.ult(self.z, const(64, USER_HI - USER_LO)),
                  S.ule(S.add(self.s, self.z), const(64, USER_HI))]
        if not self.induct:
            assume.append(S.ule(self.z, const(64, self.maxlen)))
        return params, assume

    def byte(self, addr):
        st = X.State()
        st.regions, st.mem = self.regions, S.mem_var('M')
        return X.byte(st, addr)

    def at(self, off):
        return self.match(self.byte(S.add(self.s, off)))

    def some_match(self, lo, hi):
        """A match at an offset in [lo, hi): the bytes one by one when the span
        is a known number (the terms the path's conditions hold), else
        through a free offset."""
        n = S.sub(hi, lo)
        if S.is_const(n) and n.val <= 4096:
            return [S.or_(*[self.at(S.add(lo, const(64, j))) for j in range(n.val)])]
        return [S.ule(lo, self.i), S.ult(self.i, hi), self.at(self.i)]

    def last_readable(self):
        """The last offset the scan may need, given the stop z."""
        return S.ite(S.eq(self.z, self.n), S.sub(self.n, const(64, 1)), self.z) if self.bounded else self.z

    def early(self, a, n):
        """A load past the last page the stop allows, or before s's page, is
        wrong whatever the answer."""
        out = [S.ult(page(S.add(self.s, self.last_readable())), page(S.add(a, const(64, n - 1)))), S.ult(page(a), page(self.s))]
        if self.bounded:
            out.append(S.is_zero(self.n))
        return S.or_(*out)

    def loads_unsafe(self, st, last):
        out = []
        s = self.s
        unsafe = [S.or_(S.ult(page(a), page(s)), S.ult(page(S.add(s, last)), page(S.add(a, const(64, n - 1))))) for a, n, _ in st.loads]
        if unsafe:
            if self.bounded:
                unsafe.append(S.is_zero(self.n))
            out.append(('every load in the pages of the object', [S.or_(*unsafe)]))
        mis = [S.not_(S.is_zero(S.band(a, const(64, n - 1)))) for a, n, _ in st.aligned]
        if mis:
            out.append(('aligned loads aligned', [S.or_(*mis)]))
        if st.stores:
            raise Failure('stores at %s' % ', '.join('%#x' % rip for _, _, rip in st.stores))
        return out

    def obligations(self, st):
        """At the return. Past a loop the bytes before its frontier are the
        invariant's."""
        s = self.s
        found, idx = self.answer(st)
        lo = S.sub(st.front_lo, s) if st.front_lo is not None else const(64, 0)
        out = [('the answer is a match', [found, S.not_(self.at(idx))])]
        if self.bounded:
            out.append(('the answer within the n bytes', [found, S.not_(S.ult(idx, self.n))]))
        out.append(('no match before the answer', [found] + self.some_match(lo, idx)))
        if st.front_lo is not None:
            out.append(('the answer at or past the frontier', [found, S.ult(idx, lo)]))
        last = idx
        if self.bounded:
            out.append(('no match in the n bytes when there is no answer', [S.not_(found)] + self.some_match(lo, self.n)))
            last = S.ite(found, idx, S.sub(self.n, const(64, 1)))
        for f in st.front_bytes:
            out.append(('the byte at a loop\'s frontier in the object', [S.ult(S.add(s, last), f)]))
        return out + self.loads_unsafe(st, last), [s, idx, self.z] + ([self.n] if self.bounded else [])

    def frontier_checks(self, st, f, lo):
        s = self.s
        out = [('the frontier past s', [S.ule(f, s)])]
        if lo is not None:
            out.append(('the frontier at or past the previous loop\'s', [S.ult(f, lo)]))
        if self.bounded:
            out.append(('the frontier within the object', [S.ult(self.n, S.sub(f, s))]))
        return out

    def loads_by(self, st, f, mode, step, what):
        out = []
        s = self.s
        if st.loads:
            if mode == 'B':
                bad = [S.ult(page(S.sub(f, const(64, 1))), page(S.add(a, const(64, n - 1)))) for a, n, _ in st.loads]
                out.append(('%s in the pages up to the frontier\'s' % what, [S.or_(*bad)]))
            else:
                bad = [S.ult(S.add(f, const(64, step)), S.add(a, const(64, n))) for a, n, _ in st.loads]
                out.append(('%s end by the frontier and a step' % what, [S.or_(*bad)]))
            out.append(('%s after s\'s page' % what, [S.or_(*[S.ult(page(a), page(s)) for a, _, _ in st.loads])]))
        mis = [S.not_(S.is_zero(S.band(a, const(64, n - 1)))) for a, n, _ in st.aligned]
        if mis:
            out.append(('%s: aligned ones aligned' % what, [S.or_(*mis)]))
        return out

    def base_case(self, st, f, mode, step):
        s = self.s
        lo = S.sub(st.front_lo, s) if st.front_lo is not None else const(64, 0)
        out = self.frontier_checks(st, f, st.front_lo)
        out.append(('no match before the loop', self.some_match(lo, S.sub(f, s))))
        return out + self.loads_by(st, f, mode, step, 'the loads before the loop')

    def general(self, f):
        """What the invariant at frontier f gives without a quantifier: the
        stop lies at or past it."""
        return [S.ule(S.sub(f, self.s), self.z)]

    def candidates(self, f, step):
        """Bounds on the frontier to try as part of the invariant, the strongest
        first: the next iteration's bytes inside the object (a loop that checks
        at its bottom), the frontier inside it (one that checks at its top)."""
        if not self.bounded:
            return [('none', S.TRUE)]
        off = S.sub(f, self.s)
        return [('the next iteration inside the object', S.ule(S.add(off, const(64, step)), self.n)),
                ('the frontier inside the object', S.ule(off, self.n))]

    def step_case(self, st, f, f2, mode, step):
        s = self.s
        out = [('no match in the iteration', self.some_match(S.sub(f, s), S.sub(f2, s)))]
        if self.bounded:
            out.append(('the next frontier within the object', [S.ult(self.n, S.sub(f2, s))]))
        return out + self.loads_by(st, f2, mode, step, 'the iteration\'s loads')

    def mode(self, solver, st, f, step):
        """The loads invariant this loop can have: B if the loads so far lie in
        the pages up to F - 1's, else A if F is aligned to the step and the
        loads end by F + step."""
        if not st.loads or solver.check(*st.pc, S.or_(*[S.ult(page(S.sub(f, const(64, 1))), page(S.add(a, const(64, n - 1))))
                                                         for a, n, _ in st.loads])) == 'unsat':
            return 'B'
        if step <= 4096 and solver.check(*st.pc, S.not_(S.is_zero(S.band(f, const(64, step - 1))))) == 'unsat':
            return 'A'
        return 'B'  # the base case then fails with a counterexample

    def show(self):
        return [self.s, self.z] + ([self.n] if self.bounded else [])


class Strlen(Scan):
    """strlen(s): the offset of the first zero byte."""

    def params(self):
        return [self.s]

    def match(self, b):
        return S.is_zero(b)

    def answer(self, st):
        return S.TRUE, st.gpr['rax']


class Memchr(Scan):
    """memchr(s, c, n): s + r for the first r < n with s[r] == (u8)c, else
    null; n == 0 reads nothing (s may be unreadable)."""
    bounded = True

    def params(self):
        self.c = var('c', 64)
        self.n = var('n', 64)
        return [self.s, self.c, self.n]

    def match(self, b):
        return S.eq(b, S.extract(self.c, 7, 0))

    def answer(self, st):
        r = st.gpr['rax']
        return S.not_(S.is_zero(r)), S.sub(r, self.s)


SPECS = {'strlen': Strlen, 'memchr': Memchr}


def result(args, a, what, paths, obligations, queries, seconds, detail):
    """One line per alignment class for dev/proof/run.py."""
    print('RESULT ' + json.dumps({'func': args.func, 'spec': args.spec, 'max': 'induct' if args.induct else args.max, 'align': a, 'result': what,
                                  'paths': paths, 'obligations': obligations, 'queries': queries, 'seconds': round(seconds, 1),
                                  'detail': detail}), flush=True)


def check_abi(st):
    for r in X.CALLEE_SAVED:
        if st.gpr[r] is not var('init_' + r, 64):
            raise Failure('%s not preserved' % r)
    if st.gpr['rsp'] is not S.add(var('rsp0', 64), const(64, 8)):
        raise Failure('rsp at the return: %r' % st.gpr['rsp'])


class Prover:
    def __init__(self, args, mach, spec, solver, a):
        self.args, self.mach, self.spec, self.solver, self.a = args, mach, spec, solver, a
        self.paths = self.proved = self.loops = 0
        self.weak = {}  # loop head -> the invariant candidate to start from
        self.t0 = time.time()

    def fail(self, what, st, show):
        detail = '%s; branches at %s; %s' % (what, ' '.join('%#x' % x for x in st.trace), ' '.join(self.solver.values(show).split()))
        print('FAIL %s, s %% %d == %d: %s' % (self.args.func, self.align, self.a, detail))
        result(self.args, self.a, 'fail', self.paths, self.proved, self.solver.queries, time.time() - self.t0, detail)
        sys.exit(1)

    def prove(self, st, obligations, show):
        for what, conds in obligations:
            t1 = time.time()
            r = self.solver.check(*st.pc, *conds, keep=True)
            if self.args.verbose:
                print('   %-50s %s %.1f s' % (what, r, time.time() - t1), flush=True)
            if r == 'sat':
                self.fail(what, st, show)
            if r == 'unknown':
                raise Failure('%s: z3 answered unknown' % what)
            self.proved += 1

    def generalize(self, s1, s2):
        """s1 and s2: the same path at a loop's head, the first and the second
        time. The state for every iteration, after the base case is proved."""
        moved, havoc = {}, []
        for r in s1.gpr:
            if s2.gpr[r] is s1.gpr[r]:
                continue
            d = S.sub(s2.gpr[r], s1.gpr[r])
            if S.is_const(d):
                moved[r] = S.signed(d)
            else:
                havoc.append(r)  # recomputed in the loop: any value (sound; a proof that needs it fails)
        front = [r for r in moved if X.offset_from(s1.gpr[r], self.spec.base) is not None and moved[r] > 0]
        if len(front) != 1:
            raise Failure('loop at %#x: frontier registers %s' % (s1.rip, front))
        step = moved[front[0]]  # the frontier's step; the other registers move by multiples of it (a count down, say)
        if step & (step - 1) or any(d % step for d in moved.values()):
            raise Failure('loop at %#x: steps %s, not multiples of a power of two %d' % (s1.rip, sorted(moved.values()), step))
        a1 = s1.gpr[front[0]]
        mode = self.spec.mode(self.solver, s1, a1, step)
        self.prove(s1, self.spec.base_case(s1, a1, mode, step), self.spec.show())
        cands = self.spec.candidates(a1, step)
        inv = self.weak.get(s1.rip, 0)
        while inv < len(cands) - 1 and self.solver.check(*s1.pc, S.not_(cands[inv][1])) != 'unsat':
            inv += 1  # does not hold at the first arrival: weaker
        self.prove(s1, [('the invariant at the first arrival: %s' % cands[inv][0], [S.not_(cands[inv][1])])], self.spec.show())
        self.loops += 1
        sh = step.bit_length() - 1
        k = S.concat(var('k%d_hi' % self.loops, 64 - sh), const(sh, 0))  # any multiple of the step
        g = s1.copy()
        g.fresh = 'g%d_' % self.loops
        for r, d in moved.items():
            m = d // step
            g.gpr[r] = S.add(s1.gpr[r], k) if m == 1 else S.sub(s1.gpr[r], k) if m == -1 else S.add(s1.gpr[r], S.mul(k, const(64, m)))
        for r in havoc:
            g.gpr[r] = var(g.fresh + r, 64)
        g.vec = {n: v for n, v in s1.vec.items() if all(x is y for x, y in zip(v, s2.vec.get(n, ())))}  # the same lanes: kept
        g.k = {n: v for n, v in s1.k.items() if s2.k.get(n) is v}
        g.flags = {}
        g.loads, g.aligned, g.stores = [], [], []
        g.snaps = {}
        a = g.gpr[front[0]]
        g.front_lo = a
        g.front_bytes = list(s1.front_bytes) + ([a] if mode == 'A' else [])
        g.gen_heads = list(s1.gen_heads) + [s1.rip]
        g.pc = list(s1.pc) + [S.ult(k, const(64, K_LIMIT))] + self.spec.general(a) + [self.spec.candidates(a, step)[inv][1]]
        g.gen = {'head': s1.rip, 'front': front[0], 'moved': moved, 'havoc': havoc, 'step': step, 'mode': mode, 'regs': dict(g.gpr),
                 'steps': g.steps, 'inv': inv}
        if self.args.verbose:
            print('  loop at %#x: step %d, frontier %s, loads %s, %s move, %s recomputed' % (s1.rip, step, front[0], mode, sorted(moved),
                                                                                             sorted(havoc)), flush=True)
        return g

    def back_at_head(self, st):
        """A general state's path back at its loop's head: the same form with
        K + step, the invariant kept."""
        gen = st.gen
        for r, v in gen['regs'].items():
            if r in gen['havoc']:
                continue
            if r in gen['moved']:
                ok = S.sub(st.gpr[r], v) is const(64, gen['moved'][r])
            else:
                ok = st.gpr[r] is v
            if not ok:
                raise Failure('loop at %#x: %s comes back as %r, not of the form the general state has' % (st.rip, r, st.gpr[r]))
        a, a2 = gen['regs'][gen['front']], st.gpr[gen['front']]
        cands = self.spec.candidates(a2, gen['step'])
        if self.solver.check(*st.pc, S.not_(cands[gen['inv']][1])) != 'unsat':
            if gen['inv'] + 1 < len(cands):
                raise Restart(gen['head'])
            self.prove(st, [('the invariant kept: %s' % cands[gen['inv']][0], [S.not_(cands[gen['inv']][1])])], self.spec.show())
        self.proved += 1
        self.prove(st, self.spec.step_case(st, a, a2, gen['mode'], gen['step']), self.spec.show())

    def run(self, entry, params):
        work = [X.fresh_state(entry, params, self.spec.regions)]
        targets = self.mach.targets
        while work:
            st = work.pop()
            closed = False
            while not st.done:
                if st.steps > self.args.steps:
                    raise Failure('a path past %d instructions at %#x' % (self.args.steps, st.rip))
                if self.args.induct and st.rip in targets:
                    if st.gen is not None and st.rip == st.gen['head'] and st.steps > st.gen['steps']:
                        self.back_at_head(st)
                        closed = True
                        break
                    if st.rip in st.gen_heads and st.rip != st.gen['head']:
                        raise Failure('back at the loop at %#x from inside the loop at %#x: nested loops are not done' % (st.rip, st.gen['head']))
                    snap = st.snaps.get(st.rip)
                    if snap is None:
                        st.snaps[st.rip] = st.copy()
                    else:
                        st = self.generalize(snap, st)  # a loop; after another one on this path, the next in a row
                        continue
                seen = len(st.loads)
                succ = self.mach.step(st)
                for la, ln, lrip in st.loads[seen:]:
                    if self.solver.check(*st.pc, self.spec.early(la, ln), keep=True) == 'sat':
                        self.fail('the load at %#x reaches past the string\'s last page' % lrip, st, self.spec.show())
                if len(succ) == 1:
                    st = succ[0][0]
                    continue
                live = []
                for s, c in succ:
                    if c is S.FALSE:
                        continue
                    if c is S.TRUE:
                        live.append(s)
                        continue
                    if self.solver.check(*s.pc, c) != 'unsat':
                        s.pc.append(c)
                        s.trace.append(s.rip)
                        live.append(s)
                if not live:
                    raise Failure('no feasible way on at %#x' % st.rip)
                st = live[0]
                work.extend(live[1:])
            self.paths += 1
            if closed:
                continue
            check_abi(st)
            obligations, show = self.spec.obligations(st)
            self.prove(st, obligations, show)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', required=True)
    ap.add_argument('--func', required=True)
    ap.add_argument('--spec', required=True, choices=sorted(SPECS))
    ap.add_argument('--max', type=int, default=256, help='bounded: the bound on the length or size')
    ap.add_argument('--induct', action='store_true', help='unbounded: the loops by induction')
    ap.add_argument('--steps', type=int, default=20000, help='instructions on one path at most')
    ap.add_argument('--log', help='the SMT-LIB sent to z3')
    ap.add_argument('--verbose', action='store_true', help='each obligation and its time')
    ap.add_argument('--align', default='all', help="the pointer's low bits to prove for: all, or a list")
    args = ap.parse_args()
    img = X.Image(args.elf)
    mach = X.Machine(img, bmi=not args.func.startswith(('sse2.', 'sse42.')))
    mach.targets = X.branch_targets(img) if args.induct else set()
    entry = img.symbols[args.func]
    align = align_of(img, entry)
    classes = range(align) if args.align == 'all' else [int(x) for x in args.align.split(',')]
    log = open(args.log, 'w') if args.log else None
    t0 = time.time()
    total = [0, 0, 0]
    for a in classes:
        spec = SPECS[args.spec](args.max, args.induct)
        solver = S.Solver(log=log)
        params, assume = spec.setup(a, align)
        for c in assume:
            solver.define(c)
            solver.send('(assert %s)' % solver.name(c))
        if solver.check() != 'sat':
            raise Failure('the assumptions contradict each other: the proof would be empty')
        p = Prover(args, mach, spec, solver, a)
        p.align = align
        while True:
            try:
                p.run(entry, params)
                break
            except Restart as e:
                p.weak[e.head] = p.weak.get(e.head, 0) + 1
                p.paths = p.proved = 0
                if args.verbose:
                    print('  the loop at %#x: a weaker invariant, again' % e.head, flush=True)
        print('s %% %d == %2d: %d paths, %d loops by induction, %d obligations, %d queries (%.1f s)' % (
            align, a, p.paths, p.loops, p.proved, solver.queries, time.time() - t0), flush=True)
        result(args, a, 'proved', p.paths, p.proved, solver.queries, time.time() - p.t0, '')
        total[0] += p.paths
        total[1] += p.proved
        total[2] += solver.queries
        solver.close()
    print('%s: %s proved %s, %d alignment classes: %d paths, %d obligations, %d solver queries, %.1f s' % (
        args.func, args.spec, 'for every length (loops by induction)' if args.induct else 'for lengths up to %d' % args.max, len(classes),
        total[0], total[1], total[2], time.time() - t0))


if __name__ == '__main__':
    main()
