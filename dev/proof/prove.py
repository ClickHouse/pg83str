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
ALIGN = 64  # the proof splits on a pointer's low bits: the widest vector's alignment
K_LIMIT = 1 << 40  # a loop's distance from its first iteration: below this, no address wraps


def page(a):
    return S.lshr(a, const(64, 12))


class Failure(Exception):
    pass


def aligned_pointer(name, a):
    """A pointer with its low bits a and the rest unknown: base + a, the base
    known to be ALIGN-aligned, and the memory around it an array of its own."""
    base = S.concat(var(name + '_hi', 64 - 6), const(6, 0))
    return base, S.add(base, const(64, a)), S.mem_var(name + '_mem')


class Strlen:
    """strlen(s): r, the offset of the first zero byte at s. Reads only in
    the pages of [s, s + r]; no stores; aligned loads aligned.
    The loop invariant at a frontier A (a register pointing into the
    string): no zero byte in [s, A), every load so far ends by A."""

    def __init__(self, maxlen, induct):
        self.maxlen, self.induct = maxlen, induct

    def setup(self, a):
        self.base, self.s, arr = aligned_pointer('s', a)
        self.regions = [(self.base, arr)]
        self.z = var('z', 64)  # a zero byte: the string ends at s + z or before
        self.i = var('i', 64)
        assume = [S.ule(const(64, USER_LO), self.s), S.ult(self.s, const(64, USER_HI)), S.is_zero(self.byte(S.add(self.s, self.z)))]
        if self.induct:
            assume.append(S.ult(self.z, const(64, USER_HI - USER_LO)))
        else:
            assume.append(S.ule(self.z, const(64, self.maxlen)))
        return [self.s], assume

    def byte(self, addr):
        st = X.State()
        st.regions, st.mem = self.regions, S.mem_var('M')
        return X.byte(st, addr)

    def zero_at(self, off):
        return S.is_zero(self.byte(S.add(self.s, off)))

    def some_zero(self, lo, hi):
        """A zero byte at an offset in [lo, hi): the bytes one by one when the
        span is a known number (the same terms the path's conditions hold,
        no position for the solver to match), else through a free offset."""
        n = S.sub(hi, lo)
        if S.is_const(n) and n.val <= 4096:
            return [S.or_(*[self.zero_at(S.add(lo, const(64, j))) for j in range(n.val)])]
        return [S.ule(lo, self.i), S.ult(self.i, hi), self.zero_at(self.i)]

    def early(self, a, n):
        """A load that may reach past the page of the zero at s + z, or before
        s's page, is wrong whatever the answer: the string ends by then."""
        return S.or_(S.ult(page(S.add(self.s, self.z)), page(S.add(a, const(64, n - 1)))), S.ult(page(a), page(self.s)))

    def loads_safe(self, st, end):
        out = []
        unsafe = [S.or_(S.ult(page(a), page(self.s)), S.ult(page(end), page(S.add(a, const(64, n - 1))))) for a, n, _ in st.loads]
        if unsafe:
            out.append(('every load in the pages of [s, s + r]', [S.or_(*unsafe)]))
        mis = [S.not_(S.is_zero(S.band(a, const(64, n - 1)))) for a, n, _ in st.aligned]
        if mis:
            out.append(('aligned loads aligned', [S.or_(*mis)]))
        if st.stores:
            raise Failure('stores at %s' % ', '.join('%#x' % rip for _, _, rip in st.stores))
        return out

    def obligations(self, st, frontier=None):
        """At the return; on a path from a loop's general state, the bytes
        before the frontier are the invariant's."""
        s, r, i = self.s, st.gpr['rax'], self.i
        out = [('the answer is a zero byte', [S.not_(self.zero_at(r))])]
        if frontier is None:
            out.append(('no zero byte before the answer', [S.ult(i, r), self.zero_at(i)]))
        else:
            lo = S.sub(frontier, s)
            out.append(('the answer at or past the frontier', [S.ult(r, lo)]))
            out.append(('no zero byte from the frontier to the answer', [S.ule(lo, i), S.ult(i, r), self.zero_at(i)]))
        return out + self.loads_safe(st, S.add(s, r)), [s, r, self.z]

    def base_case(self, st, a):
        """At a loop's first arrival, frontier a: the invariant holds."""
        s, i = self.s, self.i
        out = [('the frontier in the string', [S.ult(a, s)]),
               ('no zero byte before the loop', self.some_zero(const(64, 0), S.sub(a, s)))]
        if st.loads:
            out.append(('the loads before the loop end by its frontier', [S.or_(*[S.ult(a, S.add(la, const(64, n))) for la, n, _ in st.loads])]))
            out.append(('the loads before the loop after s\'s page', [S.or_(*[S.ult(page(la), page(s)) for la, _, _ in st.loads])]))
        mis = [S.not_(S.is_zero(S.band(la, const(64, n - 1)))) for la, n, _ in st.aligned]
        if mis:
            out.append(('aligned loads before the loop aligned', [S.or_(*mis)]))
        return out

    def general(self, a):
        """What the invariant at frontier a gives without a quantifier: the
        zero at s + z lies at or past it."""
        return [S.ule(S.sub(a, self.s), self.z)]

    def step_case(self, st, a, a2):
        """Back at the head, frontier a2 after a: the invariant kept."""
        s, i = self.s, self.i
        out = [('no zero byte in the iteration', self.some_zero(S.sub(a, s), S.sub(a2, s)))]
        if st.loads:
            out.append(('the iteration\'s loads end by the next frontier', [S.or_(*[S.ult(a2, S.add(la, const(64, n))) for la, n, _ in st.loads])]))
            out.append(('the iteration\'s loads after s\'s page', [S.or_(*[S.ult(page(la), page(s)) for la, _, _ in st.loads])]))
        mis = [S.not_(S.is_zero(S.band(la, const(64, n - 1)))) for la, n, _ in st.aligned]
        if mis:
            out.append(('the iteration\'s aligned loads aligned', [S.or_(*mis)]))
        return out

    def show(self):
        return [self.s, self.z]


SPECS = {'strlen': Strlen}


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
        self.t0 = time.time()

    def fail(self, what, st, show):
        detail = '%s; branches at %s; %s' % (what, ' '.join('%#x' % x for x in st.trace), ' '.join(self.solver.values(show).split()))
        print('FAIL %s, s %% %d == %d: %s' % (self.args.func, ALIGN, self.a, detail))
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
        step = None
        moved = {}
        for r in s1.gpr:
            if s2.gpr[r] is s1.gpr[r]:
                continue
            d = S.sub(s2.gpr[r], s1.gpr[r])
            if not S.is_const(d):
                raise Failure('loop at %#x: %s changes by %r, not a constant' % (s1.rip, r, d))
            moved[r] = S.signed(d)
        steps = set(moved.values())
        if len(steps) != 1:
            raise Failure('loop at %#x: the registers step by %s, not one amount' % (s1.rip, sorted(steps)))
        step = steps.pop()
        if step <= 0 or step & (step - 1):
            raise Failure('loop at %#x: a step of %d' % (s1.rip, step))
        front = [r for r in moved if X.offset_from(s1.gpr[r], self.spec.base) is not None]
        if len(front) != 1:
            raise Failure('loop at %#x: frontier registers %s' % (s1.rip, front))
        a1 = s1.gpr[front[0]]
        self.prove(s1, self.spec.base_case(s1, a1), self.spec.show())
        self.loops += 1
        sh = step.bit_length() - 1
        k = S.concat(var('k%d_hi' % self.loops, 64 - sh), const(sh, 0))  # any multiple of the step
        g = s1.copy()
        for r in moved:
            g.gpr[r] = S.add(s1.gpr[r], k)
        g.vec, g.k, g.flags = {}, {}, {}
        g.fresh = 'g%d_' % self.loops
        g.loads, g.aligned, g.stores = [], [], []
        g.snaps = {}
        a = g.gpr[front[0]]
        g.pc = list(s1.pc) + [S.ult(k, const(64, K_LIMIT))] + self.spec.general(a)
        g.gen = {'head': s1.rip, 'front': front[0], 'moved': moved, 'step': step, 'regs': dict(g.gpr), 'steps': g.steps}
        if self.args.verbose:
            print('  loop at %#x: step %d, frontier %s, %d registers move' % (s1.rip, step, front[0], len(moved)), flush=True)
        return g

    def back_at_head(self, st):
        """A general state's path back at its loop's head: the same form with
        K + step, the invariant kept."""
        gen = st.gen
        for r, v in gen['regs'].items():
            if r in gen['moved']:
                ok = S.sub(st.gpr[r], v) is const(64, gen['step'])
            else:
                ok = st.gpr[r] is v
            if not ok:
                raise Failure('loop at %#x: %s comes back as %r, not of the form the general state has' % (st.rip, r, st.gpr[r]))
        a, a2 = gen['regs'][gen['front']], st.gpr[gen['front']]
        self.prove(st, self.spec.step_case(st, a, a2), self.spec.show())

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
                    snap = st.snaps.get(st.rip)
                    if snap is None:
                        st.snaps[st.rip] = st.copy()
                    elif st.gen is None:
                        st = self.generalize(snap, st)
                        continue
                    elif st.rip != st.gen['head']:
                        raise Failure('a loop at %#x inside the loop at %#x' % (st.rip, st.gen['head']))
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
            frontier = st.gen['regs'][st.gen['front']] if st.gen else None
            obligations, show = self.spec.obligations(st, frontier)
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
    mach = X.Machine(img)
    mach.targets = X.branch_targets(img) if args.induct else set()
    entry = img.symbols[args.func]
    classes = range(ALIGN) if args.align == 'all' else [int(x) for x in args.align.split(',')]
    log = open(args.log, 'w') if args.log else None
    t0 = time.time()
    total = [0, 0, 0]
    for a in classes:
        spec = SPECS[args.spec](args.max, args.induct)
        solver = S.Solver(log=log)
        params, assume = spec.setup(a)
        for c in assume:
            solver.define(c)
            solver.send('(assert %s)' % solver.name(c))
        if solver.check() != 'sat':
            raise Failure('the assumptions contradict each other: the proof would be empty')
        p = Prover(args, mach, spec, solver, a)
        p.run(entry, params)
        print('s %% %d == %2d: %d paths, %d loops by induction, %d obligations, %d queries (%.1f s)' % (
            ALIGN, a, p.paths, p.loops, p.proved, solver.queries, time.time() - t0), flush=True)
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
