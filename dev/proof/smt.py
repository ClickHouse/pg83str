"""Bit-vector terms and a z3 session over SMT-LIB 2.

Terms are hash-consed (one object per distinct term), folded when their
arguments are constants, and sent to z3 once, each as a define-fun, the
first time an assertion needs them. Widths: n > 0 a bit-vector of n bits,
0 a Bool, -1 memory (an array from 64-bit addresses to bytes).
"""
import subprocess

BOOL, MEM = 0, -1


class T:
    __slots__ = ('op', 'w', 'args', 'val', 'id')

    def __repr__(self):
        return 'T(%s/%d%s)' % (self.op, self.w, '=%#x' % self.val if self.op in ('const', 'bconst') else '')


_table = {}
_next = [0]


def _mk(op, w, args=(), val=None):
    key = (op, w, args, val)
    t = _table.get(key)
    if t is None:
        t = T()
        t.op, t.w, t.args, t.val = op, w, args, val
        t.id = _next[0]
        _next[0] += 1
        _table[key] = t
    return t


def mask(w):
    return (1 << w) - 1


def const(w, v):
    return _mk('const', w, (), v & mask(w))


def var(name, w):
    return _mk('var', w, (name,))


TRUE = _mk('bconst', BOOL, (), 1)
FALSE = _mk('bconst', BOOL, (), 0)


def boolean(b):
    return TRUE if b else FALSE


def is_const(t):
    return t.op in ('const', 'bconst')


def signed(t):
    v = t.val
    return v - (1 << t.w) if v >> (t.w - 1) else v


# ------------------------------------------------------------- known bits

def low_zeros(t):
    """How many low bits of t are known to be zero."""
    if t.op == 'const':
        v = t.val
        return t.w if v == 0 else (v & -v).bit_length() - 1
    if t.op == 'concat':
        n = 0
        for p in reversed(t.args):
            z = low_zeros(p)
            n += z
            if z < p.w:
                break
        return n
    if t.op == 'add':
        return min(low_zeros(t.args[0]), low_zeros(t.args[1]))
    if t.op == 'and':
        return max(low_zeros(t.args[0]), low_zeros(t.args[1]))
    if t.op == 'mul':
        return min(t.w, low_zeros(t.args[0]) + low_zeros(t.args[1]))
    if t.op == 'shl' and t.args[1].op == 'const':
        return min(t.w, low_zeros(t.args[0]) + t.args[1].val)
    return 0


def split(t):
    """t as (base, constant): a sum's constant part apart."""
    if t.op == 'add' and t.args[1].op == 'const':
        return t.args[0], t.args[1].val
    if t.op == 'const':
        return None, t.val
    return t, 0


# ------------------------------------------------------------- bit-vectors

def add(a, b):
    assert a.w == b.w > 0
    if is_const(a) and is_const(b):
        return const(a.w, a.val + b.val)
    if is_const(a):
        a, b = b, a
    if is_const(b) and b.val == 0:
        return a
    if is_const(b) and a.op == 'add' and is_const(a.args[1]):
        return add(a.args[0], const(a.w, a.args[1].val + b.val))
    return _mk('add', a.w, (a, b))


def neg(a):
    if is_const(a):
        return const(a.w, -a.val)
    return _mk('neg', a.w, (a,))


def sub(a, b):
    assert a.w == b.w > 0
    ba, ca = split(a)
    bb, cb = split(b)
    if ba is bb:
        return const(a.w, ca - cb)
    if is_const(b):
        return add(a, const(a.w, -b.val))
    if a is b:
        return const(a.w, 0)
    if is_const(a) and is_const(b):
        return const(a.w, a.val - b.val)
    return _mk('sub', a.w, (a, b))


def mul(a, b):
    assert a.w == b.w > 0
    if is_const(a) and is_const(b):
        return const(a.w, a.val * b.val)
    return _mk('mul', a.w, (a, b))


def band(a, b):
    assert a.w == b.w > 0, (a, b)
    if is_const(a) and is_const(b):
        return const(a.w, a.val & b.val)
    if is_const(a):
        a, b = b, a
    if is_const(b):
        if b.val == 0:
            return b
        if b.val == mask(a.w):
            return a
        base, c = split(a)
        if base is not None:
            k = low_zeros(base)
            tz = (b.val & -b.val).bit_length() - 1  # the mask's trailing zeros
            if (~b.val & mask(a.w)) == mask(tz) and tz <= k:
                return add(base, const(a.w, c & b.val))  # (B + c) & ~(2^j - 1), B's low j bits zero
            j = b.val.bit_length()
            if b.val == mask(j) and j <= k:
                return const(a.w, c & b.val)  # (B + c) & (2^j - 1): c's low bits
    if a is b:
        return a
    return _mk('and', a.w, (a, b))


def bor(a, b):
    assert a.w == b.w > 0
    if is_const(a) and is_const(b):
        return const(a.w, a.val | b.val)
    if is_const(a):
        a, b = b, a
    if is_const(b):
        if b.val == 0:
            return a
        if b.val == mask(a.w):
            return b
    if a is b:
        return a
    return _mk('or', a.w, (a, b))


def bxor(a, b):
    assert a.w == b.w > 0
    if is_const(a) and is_const(b):
        return const(a.w, a.val ^ b.val)
    if a is b:
        return const(a.w, 0)
    if is_const(a):
        a, b = b, a
    if is_const(b) and b.val == 0:
        return a
    return _mk('xor', a.w, (a, b))


def bnot(a):
    if is_const(a):
        return const(a.w, ~a.val)
    if a.op == 'not':
        return a.args[0]
    return _mk('not', a.w, (a,))


def _shift(op, a, b, f):
    assert a.w == b.w > 0
    if is_const(b):
        if b.val >= a.w:
            return const(a.w, 0) if op != 'ashr' else ite(slt(a, const(a.w, 0)), const(a.w, -1), const(a.w, 0))
        if b.val == 0:
            return a
        if is_const(a):
            return const(a.w, f(a, b.val))
    return _mk(op, a.w, (a, b))


def shl(a, b):
    return _shift('shl', a, b, lambda a, n: a.val << n)


def lshr(a, b):
    return _shift('lshr', a, b, lambda a, n: a.val >> n)


def ashr(a, b):
    return _shift('ashr', a, b, lambda a, n: signed(a) >> n)


def extract(a, hi, lo):
    assert 0 <= lo <= hi < a.w, (a, hi, lo)
    if lo == 0 and hi == a.w - 1:
        return a
    if is_const(a):
        return const(hi - lo + 1, a.val >> lo)
    if a.op == 'extract':
        return extract(a.args[0], hi + a.args[2], lo + a.args[2])
    if a.op == 'concat':
        pos = a.w
        for p in a.args:  # most significant first
            pos -= p.w
            if lo >= pos and hi < pos + p.w:
                return extract(p, hi - pos, lo - pos)
    if a.op == 'zext':
        x = a.args[0]
        if hi < x.w:
            return extract(x, hi, lo)
        if lo >= x.w:
            return const(hi - lo + 1, 0)
    base, c = split(a)
    if base is not None and hi < low_zeros(base):
        return const(hi - lo + 1, c >> lo)  # the bits of B + c below B's known zeros are c's
    return _mk('extract', hi - lo + 1, (a, hi, lo))


def concat(*parts):
    """Most significant part first."""
    flat = []
    for p in parts:
        assert p.w > 0
        if p.op == 'concat':
            flat.extend(p.args)
        else:
            flat.append(p)
    merged = []
    for p in flat:
        if merged and is_const(merged[-1]) and is_const(p):
            q = merged.pop()
            merged.append(const(q.w + p.w, q.val << p.w | p.val))
        elif merged and p.op == 'extract' and merged[-1].op == 'extract' and merged[-1].args[0] is p.args[0] and merged[-1].args[2] == p.args[1] + 1:
            q = merged.pop()
            merged.append(extract(p.args[0], q.args[1], p.args[2]))
        else:
            merged.append(p)
    if len(merged) == 1:
        return merged[0]
    return _mk('concat', sum(p.w for p in merged), tuple(merged))


def zext(a, w):
    assert w >= a.w
    if w == a.w:
        return a
    if is_const(a):
        return const(w, a.val)
    return _mk('zext', w, (a,))


def sext(a, w):
    assert w >= a.w
    if w == a.w:
        return a
    if is_const(a):
        return const(w, signed(a))
    return _mk('sext', w, (a,))


def ite(c, a, b):
    assert c.w == BOOL and a.w == b.w
    if c is TRUE:
        return a
    if c is FALSE:
        return b
    if a is b:
        return a
    return _mk('ite', a.w, (c, a, b))


def bit(c):
    """A Bool as a 1-bit vector."""
    return ite(c, const(1, 1), const(1, 0))


# ------------------------------------------------------------- predicates

def eq(a, b):
    assert a.w == b.w, (a, b)
    if a is b:
        return TRUE
    if is_const(a) and is_const(b):
        return boolean(a.val == b.val)
    if is_const(a):
        a, b = b, a
    if a.op == 'ite' and is_const(b) and b.val == 0 and a.args[0].op in ('ult', 'ule') and a.args[0].args == (a.args[1], a.args[2]):
        return or_(eq(a.args[1], b), eq(a.args[2], b))  # an unsigned minimum is zero when one of the two is
    if a.op == 'ite' and is_const(b) and is_const(a.args[1]) and is_const(a.args[2]) and a.args[1].val != a.args[2].val:
        if a.args[1].val == b.val:
            return a.args[0]
        if a.args[2].val == b.val:
            return not_(a.args[0])
        return FALSE
    return _mk('eq', BOOL, (a, b))


def ult(a, b):
    if is_const(a) and is_const(b):
        return boolean(a.val < b.val)
    if a is b:
        return FALSE
    return _mk('ult', BOOL, (a, b))


def ule(a, b):
    if is_const(a) and is_const(b):
        return boolean(a.val <= b.val)
    if a is b:
        return TRUE
    return _mk('ule', BOOL, (a, b))


def slt(a, b):
    if is_const(a) and is_const(b):
        return boolean(signed(a) < signed(b))
    if a is b:
        return FALSE
    return _mk('slt', BOOL, (a, b))


def sle(a, b):
    if is_const(a) and is_const(b):
        return boolean(signed(a) <= signed(b))
    if a is b:
        return TRUE
    return _mk('sle', BOOL, (a, b))


def msb(a):
    return eq(extract(a, a.w - 1, a.w - 1), const(1, 1))


def is_zero(a):
    return eq(a, const(a.w, 0))


def not_(a):
    if a is TRUE:
        return FALSE
    if a is FALSE:
        return TRUE
    if a.op == 'not_b':
        return a.args[0]
    return _mk('not_b', BOOL, (a,))


def and_(*xs):
    out = []
    for x in xs:
        if x is FALSE:
            return FALSE
        if x is TRUE:
            continue
        if x.op == 'and_b':
            out.extend(x.args)
        else:
            out.append(x)
    if not out:
        return TRUE
    if len(out) == 1:
        return out[0]
    return _mk('and_b', BOOL, tuple(out))


def or_(*xs):
    out = []
    for x in xs:
        if x is TRUE:
            return TRUE
        if x is FALSE:
            continue
        if x.op == 'or_b':
            out.extend(x.args)
        else:
            out.append(x)
    if not out:
        return FALSE
    if len(out) == 1:
        return out[0]
    return _mk('or_b', BOOL, tuple(out))


# ------------------------------------------------------------- memory

def select(m, addr):
    assert m.w == MEM and addr.w == 64
    if m.op == 'store':
        base, a, v = m.args
        if a is addr:
            return v
        if is_const(a) and is_const(addr):
            return select(base, addr)
        return _mk('select', 8, (m, addr))
    return _mk('select', 8, (m, addr))


def store(m, addr, v):
    assert m.w == MEM and addr.w == 64 and v.w == 8
    return _mk('store', MEM, (m, addr, v))


def mem_var(name):
    return _mk('var', MEM, (name,))


# ------------------------------------------------------------- SMT-LIB

def sort(w):
    if w == BOOL:
        return 'Bool'
    if w == MEM:
        return '(Array (_ BitVec 64) (_ BitVec 8))'
    return '(_ BitVec %d)' % w


BIN = {'add': 'bvadd', 'sub': 'bvsub', 'mul': 'bvmul', 'and': 'bvand', 'or': 'bvor', 'xor': 'bvxor', 'shl': 'bvshl', 'lshr': 'bvlshr',
       'ashr': 'bvashr', 'ult': 'bvult', 'ule': 'bvule', 'slt': 'bvslt', 'sle': 'bvsle', 'eq': '='}


class Solver:
    """z3 -in: definitions at the base level, assertions under push/pop."""

    def __init__(self, z3='z3', log=None):
        self.p = subprocess.Popen([z3, '-in'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.done = set()
        self.log = log
        self.queries = 0
        self.send('(set-option :print-success false)')

    def send(self, s):
        if self.log:
            self.log.write(s + '\n')
        self.p.stdin.write(s + '\n')

    def name(self, t):
        if t.op == 'const':
            return '(_ bv%d %d)' % (t.val, t.w)
        if t.op == 'bconst':
            return 'true' if t.val else 'false'
        if t.op == 'var':
            return t.args[0]
        return 't%d' % t.id

    def define(self, root):
        stack = [root]
        while stack:
            t = stack[-1]
            if t.id in self.done or t.op in ('const', 'bconst'):
                stack.pop()
                continue
            pending = [a for a in t.args if isinstance(a, T) and a.id not in self.done and a.op not in ('const', 'bconst')]
            if pending:
                stack.extend(pending)
                continue
            stack.pop()
            self.done.add(t.id)
            if t.op == 'var':
                self.send('(declare-const %s %s)' % (t.args[0], sort(t.w)))
            else:
                self.send('(define-fun t%d () %s %s)' % (t.id, sort(t.w), self.expr(t)))

    def expr(self, t):
        n = self.name
        a = t.args
        if t.op in BIN:
            return '(%s %s %s)' % (BIN[t.op], n(a[0]), n(a[1]))
        if t.op == 'neg':
            return '(bvneg %s)' % n(a[0])
        if t.op == 'not':
            return '(bvnot %s)' % n(a[0])
        if t.op == 'extract':
            return '((_ extract %d %d) %s)' % (a[1], a[2], n(a[0]))
        if t.op == 'concat':
            return '(concat %s)' % ' '.join(n(p) for p in a)
        if t.op == 'zext':
            return '((_ zero_extend %d) %s)' % (t.w - a[0].w, n(a[0]))
        if t.op == 'sext':
            return '((_ sign_extend %d) %s)' % (t.w - a[0].w, n(a[0]))
        if t.op == 'ite':
            return '(ite %s %s %s)' % (n(a[0]), n(a[1]), n(a[2]))
        if t.op == 'not_b':
            return '(not %s)' % n(a[0])
        if t.op == 'and_b':
            return '(and %s)' % ' '.join(n(x) for x in a)
        if t.op == 'or_b':
            return '(or %s)' % ' '.join(n(x) for x in a)
        if t.op == 'select':
            return '(select %s %s)' % (n(a[0]), n(a[1]))
        if t.op == 'store':
            return '(store %s %s %s)' % (n(a[0]), n(a[1]), n(a[2]))
        raise ValueError(t.op)

    def check(self, *conds, keep=False):
        """sat / unsat / unknown of the conjunction, under a push that is
        popped, or with keep and a sat answer left for values()."""
        for c in conds:
            self.define(c)
        self.queries += 1
        self.send('(push 1)')
        for c in conds:
            if c is not TRUE:
                self.send('(assert %s)' % self.name(c))
        self.send('(check-sat)')
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline().strip()
            if line in ('sat', 'unsat', 'unknown'):
                break
            if line.startswith('(error') or not line:
                raise RuntimeError('z3: %s' % line)
        self.result = line
        if not (keep and line == 'sat'):
            self.send('(pop 1)')
        return line

    def values(self, terms):
        """After a sat check: the model's values of terms, then the pop."""
        for t in terms:
            self.define(t)
        self.send('(get-value (%s))' % ' '.join(self.name(t) for t in terms))
        self.p.stdin.flush()
        text, depth = '', 0
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError('z3 closed')
            text += line
            depth += line.count('(') - line.count(')')
            if depth <= 0 and text.strip():
                break
        self.send('(pop 1)')
        return text

    def close(self):
        self.p.stdin.close()
        self.p.wait()
