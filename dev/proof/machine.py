"""x86-64 semantics over smt terms, for the instructions of the library.

A State holds the registers as terms (vectors as lists of byte terms, lane 0
the lowest address), the flags as Bool terms (None where an instruction left
them undefined), memory as an array term, the path condition, and every
load and store with its address and width: the prover's obligations. The
stack lives apart, at known offsets from the entry's rsp (rsp0), where the
library keeps it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import x86  # noqa: E402
import smt as S  # noqa: E402
from smt import const, var  # noqa: E402

GPR = {}
for _i in range(16):
    GPR[x86.R64[_i]] = (x86.R64[_i], 64, 0)
    GPR[x86.R32[_i]] = (x86.R64[_i], 32, 0)
    GPR[x86.R16[_i]] = (x86.R64[_i], 16, 0)
    GPR[x86.R8[_i]] = (x86.R64[_i], 8, 0)
for _i, _n in enumerate(('ah', 'ch', 'dh', 'bh')):
    GPR[_n] = (x86.R64[_i], 8, 8)

RET_MARK = 0xdead0000beef0000
CALLEE_SAVED = ('rbx', 'rbp', 'r12', 'r13', 'r14', 'r15')


class Unsupported(Exception):
    pass


class Fault(Exception):
    """Replay: an access outside the mapped pages (a guard page natively)."""


class State:
    def __init__(self):
        self.rip = 0
        self.gpr = {}
        self.vec = {}
        self.k = {}
        self.flags = {}
        self.mem = None
        self.pc = []
        self.loads = []    # (addr, bytes, rip)
        self.aligned = []  # (addr, alignment, rip)
        self.stores = []   # (addr, bytes, rip)
        self.stack = {}    # offset from rsp0 -> 64-bit term
        self.regions = []  # (base, array): memory at base + offset as array[offset]
        self.cmem = None   # replay: (address, bytearray), the only memory there is
        self.steps = 0
        self.done = False
        self.trace = []
        self.fresh = 'init_'  # the prefix of the variables a register read before any write gets
        self.snaps = {}       # branch target -> the state at the path's first visit (loop heads, for induction)
        self.gen = None       # the loop this state stands for every iteration of
        self.front_lo = None  # the latest loop frontier: the bytes before it are its invariant's
        self.front_bytes = []  # frontiers whose byte must be in the object (loads invariant A)
        self.gen_heads = []   # the heads of the loops generalized on this path

    def copy(self):
        s = State.__new__(State)
        s.rip, s.mem, s.steps, s.done = self.rip, self.mem, self.steps, self.done
        s.gpr, s.vec, s.k, s.flags, s.stack = dict(self.gpr), dict(self.vec), dict(self.k), dict(self.flags), dict(self.stack)
        s.regions = self.regions
        s.cmem = None if self.cmem is None else (self.cmem[0], bytearray(self.cmem[1]))
        s.fresh, s.snaps, s.gen = self.fresh, dict(self.snaps), self.gen
        s.front_lo, s.front_bytes, s.gen_heads = self.front_lo, list(self.front_bytes), list(self.gen_heads)
        s.pc, s.loads, s.aligned, s.stores, s.trace = list(self.pc), list(self.loads), list(self.aligned), list(self.stores), list(self.trace)
        return s


def fresh_state(entry, args, regions=()):
    """The state at a function's entry: args are terms for rdi, rsi, rdx, rcx,
    r8, r9 in order; every other register a variable of its own."""
    st = State()
    st.rip = entry
    for r in x86.R64:
        st.gpr[r] = var('init_' + r, 64)
    for r, t in zip(('rdi', 'rsi', 'rdx', 'rcx', 'r8', 'r9'), args):
        st.gpr[r] = t
    st.gpr['rsp'] = var('rsp0', 64)
    st.stack[0] = const(64, RET_MARK)
    st.mem = S.mem_var('M')
    st.regions = list(regions)
    return st


# ------------------------------------------------------------- registers

def vec_index(name):
    for p, n in (('xmm', 16), ('ymm', 32), ('zmm', 64)):
        if name.startswith(p):
            return int(name[3:]), n
    return None


def read_reg(st, name):
    if name in GPR:
        full, w, lo = GPR[name]
        return S.extract(st.gpr[full], lo + w - 1, lo)
    if name.startswith('k') and name[1:].isdigit():
        if name not in st.k:
            st.k[name] = var(st.fresh + name, 64)
        return st.k[name]
    raise Unsupported('register %s' % name)


def write_reg(st, name, v):
    full, w, lo = GPR[name]
    assert v.w == w, (name, v)
    if w == 64:
        st.gpr[full] = v
    elif w == 32:
        st.gpr[full] = S.zext(v, 64)
    else:
        old = st.gpr[full]
        parts = []
        if lo + w < 64:
            parts.append(S.extract(old, 63, lo + w))
        parts.append(v)
        if lo:
            parts.append(S.extract(old, lo - 1, 0))
        st.gpr[full] = S.concat(*parts)


def read_vec(st, name):
    n, width = vec_index(name)
    lanes = st.vec.get(n)
    if lanes is None:
        lanes = [var('%szmm%d_%d' % (st.fresh, n, i), 8) for i in range(64)]
        st.vec[n] = lanes
    return lanes[:width]


def write_vec(st, name, lanes, legacy=False):
    """VEX and EVEX writes zero the lanes above the operand; legacy SSE keeps them."""
    n, width = vec_index(name)
    assert len(lanes) == width
    if legacy:
        old = read_vec(st, 'zmm%d' % n)
        st.vec[n] = list(lanes) + old[width:]
    else:
        st.vec[n] = list(lanes) + [const(8, 0)] * (64 - width)


def write_k(st, name, v, width):
    """A mask register written with width bits: the rest zeroed."""
    st.k[name] = S.zext(v, 64) if v.w < 64 else v


def to_bv(lanes):
    """Byte lanes as one vector, lane 0 least significant."""
    return S.concat(*reversed(lanes))


def bits(bools):
    """Bools as a bit-vector, the first least significant."""
    return S.concat(*[S.bit(b) for b in reversed(bools)])


# ------------------------------------------------------------- memory

class Image:
    """The linked library: its sections by address, its symbols."""

    def __init__(self, path):
        import elf
        e = elf.read(path, executable=True)
        self.sections = [s for s in e.sections if s.addr and s.type in ('SHT_PROGBITS', 'SHT_NOBITS')]
        self.symbols = {s.name: s.value for s in e.symbols if s.name and s.type in ('STT_FUNC', 'STT_OBJECT', 'STT_NOTYPE')}
        self.sizes = {s.name: s.size for s in e.symbols if s.name and s.type == 'STT_FUNC'}
        self.names = {}
        for s in e.symbols:
            if s.name and s.type in ('STT_FUNC', 'STT_NOTYPE') and s.value:
                self.names.setdefault(s.value, s.name)
        self.text = next(s for s in self.sections if s.name == '.text')
        self.cache = {}
        self.overrides = {}  # address -> byte: the table a configuration installs

    def section_at(self, a):
        for s in self.sections:
            if s.addr <= a < s.addr + s.size:
                return s
        return None

    def byte(self, a):
        if a in self.overrides:
            return self.overrides[a]
        s = self.section_at(a)
        return 0 if s.type == 'SHT_NOBITS' else s.data[a - s.addr]

    def decode(self, a):
        j = self.cache.get(a)
        if j is None:
            t = self.text
            if not t.addr <= a < t.addr + t.size:
                raise Unsupported('jump outside .text: %#x' % a)
            j = x86.decode_one(t.data, t.addr, a - t.addr)
            self.cache[a] = j
        return j


def addends(t):
    if t.op == 'add':
        return addends(t.args[0]) + addends(t.args[1])
    return [t]


def offset_from(addr, base):
    """addr - base when addr is a sum with base as one of its terms, else None."""
    terms = addends(addr)
    if base not in terms:
        return None
    terms.remove(base)
    off = const(64, 0)
    for x in terms:
        off = S.add(off, x)
    return off


def byte(st, addr):
    """The byte at addr: in a region, an element of the region's array at the
    offset from its base (the solver then compares offsets, not addresses)."""
    for base, arr in st.regions:
        off = offset_from(addr, base)
        if off is not None:
            return S.select(arr, off)
    return S.select(st.mem, addr)


def stack_offset(st, addr):
    """addr as rsp0 + offset, or None."""
    rsp0 = var('rsp0', 64)
    if addr is rsp0:
        return 0
    if addr.op == 'add' and addr.args[0] is rsp0 and S.is_const(addr.args[1]):
        return S.signed(addr.args[1])
    return None


def load(m, st, addr, n, rip):
    """n bytes at addr as lanes."""
    off = stack_offset(st, addr)
    if off is not None:
        if n != 8 or off not in st.stack:
            raise Unsupported('stack load of %d bytes at rsp0%+d' % (n, off))
        v = st.stack[off]
        return [S.extract(v, 8 * i + 7, 8 * i) for i in range(8)]
    if S.is_const(addr) and m.image.section_at(addr.val) is not None:
        return [const(8, m.image.byte(addr.val + i)) for i in range(n)]
    if st.cmem is not None:
        base, buf = st.cmem
        if not S.is_const(addr) or not base <= addr.val <= addr.val + n <= base + len(buf):
            raise Fault('load of %d bytes at %r from %#x' % (n, addr, rip))
        o = addr.val - base
        return [const(8, b) for b in buf[o:o + n]]
    st.loads.append((addr, n, rip))
    return [byte(st, S.add(addr, const(64, i))) for i in range(n)]


def store_bytes(m, st, addr, lanes, rip):
    off = stack_offset(st, addr)
    if off is not None:
        if len(lanes) != 8:
            raise Unsupported('stack store of %d bytes' % len(lanes))
        st.stack[off] = to_bv(lanes)
        return
    if st.cmem is not None:
        base, buf = st.cmem
        if not S.is_const(addr) or not base <= addr.val <= addr.val + len(lanes) <= base + len(buf):
            raise Fault('store of %d bytes at %r from %#x' % (len(lanes), addr, rip))
        for i, b in enumerate(lanes):
            if not S.is_const(b):
                raise Fault('store of an unknown byte at %#x' % rip)
            buf[addr.val - base + i] = b.val
        return
    st.stores.append((addr, len(lanes), rip))
    for i, b in enumerate(lanes):
        st.mem = S.store(st.mem, S.add(addr, const(64, i)), b)


def mem_addr(st, j, mem):
    if mem['rip_rel']:
        return const(64, j['offset'] + j['len'] + mem['disp'])
    a = const(64, mem['disp'])
    if mem['base'] not in (None, 'none'):
        a = S.add(read_reg(st, mem['base']), a)
    if mem['index'] not in (None, 'none'):
        a = S.add(a, S.mul(read_reg(st, mem['index']), const(64, mem['scale'])))
    return a


# ------------------------------------------------------------- operands

def op_value(m, st, j, o, w):
    """A general-purpose operand as a w-bit term."""
    if o['kind'] == 'Register':
        return read_reg(st, o['reg'])
    if o['kind'] == 'Immediate':
        v = const(o['size'], o['imm'])
        return S.sext(v, w) if o.get('signed') else S.zext(v, w)
    if o['kind'] == 'Memory':
        return to_bv(load(m, st, mem_addr(st, j, o['mem']), w // 8, j['offset']))
    raise Unsupported(o['kind'])


def op_store(m, st, j, o, v):
    if o['kind'] == 'Register':
        write_reg(st, o['reg'], v)
    elif o['kind'] == 'Memory':
        store_bytes(m, st, mem_addr(st, j, o['mem']), [S.extract(v, 8 * i + 7, 8 * i) for i in range(v.w // 8)], j['offset'])
    else:
        raise Unsupported(o['kind'])


def vec_operand(m, st, j, o, nbytes, aligned=False):
    if o['kind'] == 'Register':
        if o['reg'] in GPR:
            raise Unsupported('gpr as vector')
        return read_vec(st, o['reg'])[:nbytes]
    addr = mem_addr(st, j, o['mem'])
    if aligned:
        st.aligned.append((addr, nbytes, j['offset']))
    return load(m, st, addr, nbytes, j['offset'])


# ------------------------------------------------------------- flags

def flags_logic(st, r):
    st.flags = {'CF': S.FALSE, 'OF': S.FALSE, 'ZF': S.is_zero(r), 'SF': S.msb(r), 'PF': None}


def flags_add(st, a, b, r):
    st.flags = {'CF': S.ult(r, a), 'ZF': S.is_zero(r), 'SF': S.msb(r), 'OF': S.and_(S.eq(S.msb(a), S.msb(b)), S.not_(S.eq(S.msb(r), S.msb(a)))),
                'PF': None}


def flags_sub(st, a, b, r):
    st.flags = {'CF': S.ult(a, b), 'ZF': S.eq(a, b), 'SF': S.msb(r), 'OF': S.and_(S.not_(S.eq(S.msb(a), S.msb(b))), S.not_(S.eq(S.msb(r), S.msb(a)))),
                'PF': None}


def flag(st, name):
    f = st.flags.get(name)
    if f is None:
        raise Unsupported('flag %s undefined here' % name)
    return f


def condition(st, cc):
    f = lambda n: flag(st, n)
    return {
        'o': lambda: f('OF'), 'no': lambda: S.not_(f('OF')), 'b': lambda: f('CF'), 'ae': lambda: S.not_(f('CF')), 'e': lambda: f('ZF'),
        'ne': lambda: S.not_(f('ZF')), 'be': lambda: S.or_(f('CF'), f('ZF')), 'a': lambda: S.and_(S.not_(f('CF')), S.not_(f('ZF'))),
        's': lambda: f('SF'), 'ns': lambda: S.not_(f('SF')), 'l': lambda: S.not_(S.eq(S.bit(f('SF')), S.bit(f('OF')))),
        'ge': lambda: S.eq(S.bit(f('SF')), S.bit(f('OF'))),
        'le': lambda: S.or_(f('ZF'), S.not_(S.eq(S.bit(f('SF')), S.bit(f('OF'))))),
        'g': lambda: S.and_(S.not_(f('ZF')), S.eq(S.bit(f('SF')), S.bit(f('OF')))),
    }[cc]()


def tzcnt(x):
    r = const(x.w, x.w)
    for i in reversed(range(x.w)):
        r = S.ite(S.eq(S.extract(x, i, i), const(1, 1)), const(x.w, i), r)
    return r


# ------------------------------------------------------------- execution

CC = set(x86.CC)


def branch_targets(image):
    """Every direct branch target in the code: a linear sweep of each function."""
    out = set()
    t = image.text
    for name, start in image.symbols.items():
        size = image.sizes.get(name, 0)
        if not (t.addr <= start < t.addr + t.size) or not size:
            continue
        a = start
        while a < start + size:
            try:
                j = image.decode(a)
            except Exception:
                a += 1
                continue
            if j.get('branch_target') is not None:
                out.add(j['branch_target'])
            a += j['len']
    return out


class Machine:
    def __init__(self, image, bmi=True):
        self.image = image
        self.bmi = bmi  # False: tzcnt runs as bsf (a CPU without BMI1, the sse2 and sse42 configurations' targets)
        self.fresh_n = 0

    def step(self, st):
        """Execute one instruction: [(state, condition)] successors, where a
        conditional branch gives two with their conditions (TRUE otherwise)."""
        j = self.image.decode(st.rip)
        st.steps += 1
        mn = j['mnemonic']
        ops = j['ops']
        nxt = j['offset'] + j['len']
        if j.get('op_mask') not in (None, 'none'):
            raise Unsupported('%s with a mask at %#x' % (mn, st.rip))
        if mn.startswith('nop') or mn in ('endbr64',):
            st.rip = nxt
            return [(st, S.TRUE)]
        if mn == 'ret':
            off = stack_offset(st, read_reg(st, 'rsp'))
            if off is None or off not in st.stack:
                raise Unsupported('ret with an unknown stack')
            target = st.stack[off]
            st.gpr['rsp'] = S.add(st.gpr['rsp'], const(64, 8))
            if S.is_const(target) and target.val == RET_MARK:
                st.done = True
                return [(st, S.TRUE)]
            if not S.is_const(target):
                raise Unsupported('ret to a computed address')
            st.rip = target.val
            return [(st, S.TRUE)]
        if mn == 'jmp':
            o = ops[0]
            if o['kind'] == 'NearBranch':
                st.rip = j['branch_target']
                return [(st, S.TRUE)]
            t = op_value(self, st, j, o, 64)
            if not S.is_const(t):
                raise Unsupported('indirect jump to a computed address at %#x' % st.rip)
            st.rip = t.val
            return [(st, S.TRUE)]
        if mn == 'call':
            o = ops[0]
            target = j['branch_target'] if o['kind'] == 'NearBranch' else None
            if target is None:
                t = op_value(self, st, j, o, 64)
                if not S.is_const(t):
                    raise Unsupported('indirect call to a computed address')
                target = t.val
            rsp = S.sub(st.gpr['rsp'], const(64, 8))
            st.gpr['rsp'] = rsp
            off = stack_offset(st, rsp)
            if off is None:
                raise Unsupported('call with an unknown stack')
            st.stack[off] = const(64, nxt)
            st.rip = target
            return [(st, S.TRUE)]
        if mn.startswith('j') and mn[1:] in CC:
            c = condition(st, mn[1:])
            taken, fall = st, st.copy()
            taken.rip, fall.rip = j['branch_target'], nxt
            return [(taken, c), (fall, S.not_(c))]
        f = getattr(self, 'i_' + mn, None)
        if f is None:
            raise Unsupported('%s (%s) at %#x' % (mn, j['gas'], st.rip))
        f(st, j, ops)
        st.rip = nxt
        return [(st, S.TRUE)]

    # --- general purpose
    def i_mov(self, st, j, ops):
        w = ops[0]['size'] if ops[0]['kind'] == 'Register' else ops[0]['mem']['size'] * 8
        op_store(self, st, j, ops[0], op_value(self, st, j, ops[1], w))

    def i_neg(self, st, j, ops):
        w = ops[0]['size']
        a = op_value(self, st, j, ops[0], w)
        r = S.neg(a)
        st.flags = {'CF': S.not_(S.is_zero(a)), 'ZF': S.is_zero(r), 'SF': S.msb(r), 'OF': S.eq(a, const(w, 1 << (w - 1))), 'PF': None}
        op_store(self, st, j, ops[0], r)

    def i_not(self, st, j, ops):
        w = ops[0]['size']
        op_store(self, st, j, ops[0], S.bnot(op_value(self, st, j, ops[0], w)))

    def _cmov(self, st, j, ops, cc):
        w = ops[0]['size']
        src = op_value(self, st, j, ops[1], w)  # read (and loaded from memory) whatever the condition
        old = read_reg(st, ops[0]['reg'])
        write_reg(st, ops[0]['reg'], S.ite(condition(st, cc), src, old))  # a 32-bit cmov zero-extends either way: write_reg does

    def i_movzx(self, st, j, ops):
        w = ops[0]['size']
        src = op_value(self, st, j, ops[1], ops[1]['size'])
        write_reg(st, ops[0]['reg'], S.zext(src, w))

    def i_movsx(self, st, j, ops):
        w = ops[0]['size']
        write_reg(st, ops[0]['reg'], S.sext(op_value(self, st, j, ops[1], ops[1]['size']), w))

    i_movabs = i_mov
    i_movsxd = i_movsx
    i_movzb = i_movzw = i_movzbl = i_movzwl = i_movzx
    i_movsb = i_movsw = i_movsx

    def _alu(self, st, j, ops, fn, flags, store=True):
        w = ops[0]['size']
        a = op_value(self, st, j, ops[0], w)
        b = op_value(self, st, j, ops[1], w)
        r = fn(a, b)
        flags(st, a, b, r)
        if store:
            op_store(self, st, j, ops[0], r)

    def i_add(self, st, j, ops):
        self._alu(st, j, ops, S.add, flags_add)

    def i_sub(self, st, j, ops):
        self._alu(st, j, ops, S.sub, flags_sub)

    def i_cmp(self, st, j, ops):
        self._alu(st, j, ops, S.sub, flags_sub, store=False)

    def i_and(self, st, j, ops):
        self._alu(st, j, ops, S.band, lambda st, a, b, r: flags_logic(st, r))

    def i_or(self, st, j, ops):
        self._alu(st, j, ops, S.bor, lambda st, a, b, r: flags_logic(st, r))

    def i_xor(self, st, j, ops):
        self._alu(st, j, ops, S.bxor, lambda st, a, b, r: flags_logic(st, r))

    def i_test(self, st, j, ops):
        self._alu(st, j, ops, S.band, lambda st, a, b, r: flags_logic(st, r), store=False)

    def i_lea(self, st, j, ops):
        a = mem_addr(st, j, ops[1]['mem'])
        w = ops[0]['size']
        write_reg(st, ops[0]['reg'], S.extract(a, w - 1, 0))

    def i_tzcnt(self, st, j, ops):
        if not self.bmi:
            return self.i_bsf(st, j, ops)
        w = ops[0]['size']
        x = op_value(self, st, j, ops[1], w)
        r = tzcnt(x)
        st.flags = {'CF': S.is_zero(x), 'ZF': S.is_zero(r), 'SF': None, 'OF': None, 'PF': None}
        write_reg(st, ops[0]['reg'], r)

    def i_bsf(self, st, j, ops):
        """The lowest set bit; with a zero source the destination is left
        undefined (an unknown value here) and ZF set."""
        w = ops[0]['size']
        x = op_value(self, st, j, ops[1], w)
        self.fresh_n += 1
        r = S.ite(S.is_zero(x), var('%sbsf%d' % (st.fresh, self.fresh_n), w), tzcnt(x))
        st.flags = {'ZF': S.is_zero(x), 'CF': None, 'SF': None, 'OF': None, 'PF': None}
        write_reg(st, ops[0]['reg'], r)

    def _shift(self, st, j, ops, fn, out_bit):
        w = ops[0]['size']
        a = op_value(self, st, j, ops[0], w)
        cnt = ops[1] if len(ops) > 1 else {'kind': 'Immediate', 'imm': 1, 'size': 8}
        if cnt['kind'] == 'Immediate':
            n = cnt['imm'] & (63 if w == 64 else 31)
            if n == 0:
                return
            r = fn(a, const(w, n))
            cf = S.eq(S.extract(a, out_bit(w, n), out_bit(w, n)), const(1, 1))
            st.flags = {'CF': cf, 'ZF': S.is_zero(r), 'SF': S.msb(r), 'OF': None, 'PF': None}
        else:
            n = S.band(S.zext(read_reg(st, cnt['reg']), w), const(w, 63 if w == 64 else 31))
            r = fn(a, n)
            st.flags = {'CF': None, 'ZF': None, 'SF': None, 'OF': None, 'PF': None}  # unchanged for a count of 0: not modelled
        op_store(self, st, j, ops[0], r)

    def i_shr(self, st, j, ops):
        self._shift(st, j, ops, S.lshr, lambda w, n: n - 1)

    def i_shl(self, st, j, ops):
        self._shift(st, j, ops, S.shl, lambda w, n: w - n)

    def i_sar(self, st, j, ops):
        self._shift(st, j, ops, S.ashr, lambda w, n: n - 1)

    def _shiftx(self, st, j, ops, fn):
        w = ops[0]['size']
        x = op_value(self, st, j, ops[1], w)
        n = S.band(op_value(self, st, j, ops[2], w), const(w, w - 1))
        write_reg(st, ops[0]['reg'], fn(x, n))

    def i_shrx(self, st, j, ops):
        self._shiftx(st, j, ops, S.lshr)

    def i_shlx(self, st, j, ops):
        self._shiftx(st, j, ops, S.shl)

    def i_sarx(self, st, j, ops):
        self._shiftx(st, j, ops, S.ashr)

    # --- mask registers
    def _kmov(self, st, j, ops, width):
        d, s = ops
        if s['kind'] == 'Register' and s['reg'].startswith('k') and s['reg'][1:].isdigit():
            v = S.extract(read_reg(st, s['reg']), width - 1, 0)
        else:
            v = S.extract(op_value(self, st, j, s, s['size']), width - 1, 0)
        if d['kind'] == 'Register' and d['reg'] in GPR:
            write_reg(st, d['reg'], S.zext(v, d['size']))
        elif d['kind'] == 'Register':
            write_k(st, d['reg'], v, width)
        else:
            op_store(self, st, j, d, v)

    def i_kmovq(self, st, j, ops):
        self._kmov(st, j, ops, 64)

    def i_kmovd(self, st, j, ops):
        self._kmov(st, j, ops, 32)

    def i_kmovw(self, st, j, ops):
        self._kmov(st, j, ops, 16)

    def i_kmovb(self, st, j, ops):
        self._kmov(st, j, ops, 8)

    def _kortest(self, st, j, ops, width):
        r = S.extract(S.bor(read_reg(st, ops[0]['reg']), read_reg(st, ops[1]['reg'])), width - 1, 0)
        st.flags = {'ZF': S.is_zero(r), 'CF': S.eq(r, const(width, -1)), 'SF': S.FALSE, 'OF': S.FALSE, 'PF': S.FALSE}

    def i_kortestq(self, st, j, ops):
        self._kortest(st, j, ops, 64)

    def i_kortestd(self, st, j, ops):
        self._kortest(st, j, ops, 32)

    def i_kortestw(self, st, j, ops):
        self._kortest(st, j, ops, 16)

    # --- vectors
    def _width(self, ops):
        for o in ops:
            if o['kind'] == 'Register' and vec_index(o['reg']):
                return vec_index(o['reg'])[1]
        raise Unsupported('no vector operand')

    def _vmov(self, st, j, ops, aligned):
        d, s = ops
        n = self._width(ops)
        legacy = j.get('encoding') == 'Legacy'
        if d['kind'] == 'Register':
            write_vec(st, d['reg'], vec_operand(self, st, j, s, n, aligned), legacy)
        else:
            lanes = read_vec(st, s['reg'])[:n]
            addr = mem_addr(st, j, d['mem'])
            if aligned:
                st.aligned.append((addr, n, j['offset']))
            store_bytes(self, st, addr, lanes, j['offset'])

    def i_vmovdqu64(self, st, j, ops):
        self._vmov(st, j, ops, False)

    i_vmovdqu32 = i_vmovdqu8 = i_vmovdqu16 = i_vmovdqu = i_vmovups = i_movdqu = i_movups = i_vmovdqu64

    def i_vmovdqa64(self, st, j, ops):
        self._vmov(st, j, ops, True)

    i_vmovdqa32 = i_vmovdqa = i_vmovaps = i_movdqa = i_movaps = i_vmovdqa64

    def _vtestnm(self, st, j, ops, want_zero):
        n = self._width(ops)
        a = vec_operand(self, st, j, ops[1], n)
        b = vec_operand(self, st, j, ops[2], n)
        bs = [S.is_zero(S.band(x, y)) for x, y in zip(a, b)]
        if not want_zero:
            bs = [S.not_(x) for x in bs]
        write_k(st, ops[0]['reg'], bits(bs), n)

    def i_vptestnmb(self, st, j, ops):
        self._vtestnm(st, j, ops, True)

    def i_vptestmb(self, st, j, ops):
        self._vtestnm(st, j, ops, False)

    def _lanewise(self, st, j, ops, fn):
        n = self._width(ops)
        if len(ops) == 3:
            a = vec_operand(self, st, j, ops[1], n)
            b = vec_operand(self, st, j, ops[2], n)
            legacy = False
        else:
            a = vec_operand(self, st, j, ops[0], n)
            b = vec_operand(self, st, j, ops[1], n, aligned=True)
            legacy = True
        write_vec(st, ops[0]['reg'], [fn(x, y) for x, y in zip(a, b)], legacy)

    def i_vpminub(self, st, j, ops):
        self._lanewise(st, j, ops, lambda x, y: S.ite(S.ult(x, y), x, y))

    i_pminub = i_vpminub

    def i_movd(self, st, j, ops):
        d, s = ops
        if d['kind'] == 'Register' and vec_index(d['reg']):
            w = s['size'] if s['kind'] == 'Register' else 32 if j['mnemonic'].endswith('d') else 64
            v = op_value(self, st, j, s, w)
            n = vec_index(d['reg'])[1]
            lanes = [S.extract(v, 8 * i + 7, 8 * i) for i in range(w // 8)] + [const(8, 0)] * (16 - w // 8)
            if j.get('encoding') == 'Legacy':
                write_vec(st, 'xmm%d' % vec_index(d['reg'])[0], lanes, legacy=True)
            else:
                write_vec(st, 'xmm%d' % vec_index(d['reg'])[0], lanes)
        else:
            lanes = read_vec(st, s['reg'])
            w = d['size'] if d['kind'] == 'Register' else 32 if j['mnemonic'].endswith('d') else 64
            op_store(self, st, j, d, to_bv(lanes[:w // 8]))

    i_vmovd = i_movq = i_vmovq = i_movd

    def i_punpcklbw(self, st, j, ops):
        n = self._width(ops)
        legacy = len(ops) == 2
        a = vec_operand(self, st, j, ops[0 if legacy else 1], n)
        b = vec_operand(self, st, j, ops[1 if legacy else 2], n, aligned=legacy)
        out = []
        for lane in range(0, n, 16):  # per 128-bit lane: the low 8 bytes of each, interleaved
            for i in range(8):
                out += [a[lane + i], b[lane + i]]
        write_vec(st, ops[0]['reg'], out, legacy)

    i_vpunpcklbw = i_punpcklbw

    def i_pshuflw(self, st, j, ops):
        n = self._width(ops)
        legacy = j.get('encoding') == 'Legacy'
        src = vec_operand(self, st, j, ops[1], n, aligned=legacy)
        imm = ops[2]['imm']
        out = []
        for lane in range(0, n, 16):
            words = [src[lane + 2 * w:lane + 2 * w + 2] for w in range(8)]
            lo = [words[(imm >> (2 * k)) & 3] for k in range(4)]
            out += [b for w in lo + words[4:] for b in w]
        write_vec(st, ops[0]['reg'], out, legacy)

    def i_pshufd(self, st, j, ops):
        n = self._width(ops)
        legacy = j.get('encoding') == 'Legacy'
        src = vec_operand(self, st, j, ops[1], n, aligned=legacy)
        imm = ops[2]['imm']
        out = []
        for lane in range(0, n, 16):
            dw = [src[lane + 4 * w:lane + 4 * w + 4] for w in range(4)]
            out += [b for k in range(4) for b in dw[(imm >> (2 * k)) & 3]]
        write_vec(st, ops[0]['reg'], out, legacy)

    i_vpshufd = i_pshufd

    def i_vpbroadcastb(self, st, j, ops):
        n = self._width(ops[:1])
        s = ops[1]
        if s['kind'] == 'Register' and s['reg'] in GPR:
            b = S.extract(read_reg(st, s['reg']), 7, 0)
        elif s['kind'] == 'Register':
            b = read_vec(st, s['reg'])[0]
        else:
            b = load(self, st, mem_addr(st, j, s['mem']), 1, j['offset'])[0]
        write_vec(st, ops[0]['reg'], [b] * n)

    def i_pcmpeqb(self, st, j, ops):
        self._lanewise(st, j, ops, lambda x, y: S.ite(S.eq(x, y), const(8, 0xff), const(8, 0)))

    def i_vpcmpeqb(self, st, j, ops):
        if ops[0]['kind'] == 'Register' and ops[0]['reg'].startswith('k'):
            n = self._width(ops)
            a = vec_operand(self, st, j, ops[1], n)
            b = vec_operand(self, st, j, ops[2], n)
            write_k(st, ops[0]['reg'], bits([S.eq(x, y) for x, y in zip(a, b)]), n)
        else:
            self.i_pcmpeqb(st, j, ops)

    def i_pxor(self, st, j, ops):
        self._lanewise(st, j, ops, S.bxor)

    i_vpxor = i_vpxord = i_vpxorq = i_xorps = i_vxorps = i_pxor

    def i_por(self, st, j, ops):
        self._lanewise(st, j, ops, S.bor)

    i_vpor = i_vpord = i_vporq = i_por

    def i_pand(self, st, j, ops):
        self._lanewise(st, j, ops, S.band)

    i_vpand = i_vpandd = i_vpandq = i_pand

    def i_pmovmskb(self, st, j, ops):
        n = self._width(ops)
        lanes = vec_operand(self, st, j, ops[1], n)
        m = bits([S.msb(x) for x in lanes])
        write_reg(st, ops[0]['reg'], S.zext(m, ops[0]['size']) if m.w < ops[0]['size'] else S.extract(m, ops[0]['size'] - 1, 0))

    i_vpmovmskb = i_pmovmskb

    def i_vzeroupper(self, st, j, ops):
        for n in range(16):
            lanes = read_vec(st, 'zmm%d' % n)
            st.vec[n] = lanes[:16] + [const(8, 0)] * 48


for _cc in x86.CC:
    setattr(Machine, 'i_cmov' + _cc, (lambda cc: lambda self, st, j, ops: self._cmov(st, j, ops, cc))(_cc))
    setattr(Machine, 'i_set' + _cc, (lambda cc: lambda self, st, j, ops: op_store(self, st, j, ops[0], S.zext(S.bit(condition(st, cc)), 8)))(_cc))
