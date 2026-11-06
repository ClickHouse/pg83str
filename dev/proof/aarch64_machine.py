"""AArch64 instruction semantics over the existing SMT/memory term layer.

Only explicitly implemented instructions execute. Capstone supplies decoding,
not semantics. Unsupported instructions, computed unknown branches and invalid
ELF architectures fail closed. This backend is experimental until its semantics
have been replayed against hardware and mutation tests cover its proof gates.
"""
import capstone
from capstone import arm64 as A
import machine as B
from machine import State, Unsupported, Fault, byte, region_of, rounded, offset_from, read_vec
import smt as S
import aarch64_vectors
from smt import const, var

ARG_REGS = ('rax', 'rsi', 'rdx', 'rcx', 'r8', 'r9', 'r10', 'r11')
NAMES = ARG_REGS + tuple('a%d' % i for i in range(8, 31))
CALLEE_SAVED = tuple('a%d' % i for i in range(19, 30))
VEC_CALLEE_SAVED = tuple((i, 8) for i in range(8, 16))
RETURN_SP_DELTA = 0
CONCRETE_KEEP = ('rsp', 'a30')
RSP0 = S.concat(var('rsp0_hi', 60), const(4, 0))
RET_MARK = B.RET_MARK
GPR = {name: (NAMES[i], 64 if name[0] == 'x' else 32, 0)
       for i in range(31) for name in ('x%d' % i, 'w%d' % i)}
GPR.update({'sp': ('rsp', 64, 0), 'wsp': ('rsp', 32, 0)})


def reg_name(name):
    return {'fp': 'x29', 'lr': 'x30'}.get(name, name)


def fresh_state(entry, args, regions=(), aliases=()):
    st = State()
    st.rip = entry
    st.stack_base = RSP0
    st.gpr = {r: var('init_' + r, 64) for r in NAMES}
    st.gpr.update(zip(ARG_REGS, args))
    st.gpr['rsp'] = RSP0
    st.gpr['a30'] = const(64, RET_MARK)
    st.mem = S.mem_var('M')
    st.regions = list(regions) + [(RSP0, S.mem_var('stack'))]
    st.aliases = list(aliases)
    for n in range(32):
        read_vec(st, 'zmm%d' % n)
    return st


class Image(B.Image):
    def __init__(self, path, sve_vl=None):
        if sve_vl not in (None, 16):
            raise Unsupported('the experimental SVE model currently requires VL=16 bytes')
        self.sve_vl = sve_vl
        super().__init__(path, machine=183)
        self.decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
        self.decoder.detail = True

    def decode(self, address):
        if address in self.cache:
            return self.cache[address]
        section = self.section_at(address)
        if section is None or section.name != '.text' or address & 3:
            raise Unsupported('AArch64 branch outside aligned .text: %#x' % address)
        raw = section.data[address - section.addr:address - section.addr + 4]
        ins = next(self.decoder.disasm(raw, address, count=1), None)
        if ins is None or ins.size != 4:
            raise Unsupported('undecodable AArch64 instruction at %#x' % address)
        ops = []
        for op in ins.operands:
            if op.type == A.ARM64_OP_REG:
                name = reg_name(ins.reg_name(op.reg))
                size = 64 if name.startswith('x') or name == 'sp' else 32
                ops.append({'kind': 'Register', 'reg': name, 'size': size,
                            'shift': (op.shift.type, op.shift.value), 'ext': op.ext, 'vas': op.vas,
                            'vector_index': op.vector_index})
            elif op.type == A.ARM64_OP_IMM:
                ops.append({'kind': 'Immediate', 'imm': op.imm, 'size': 64,
                            'shift': (op.shift.type, op.shift.value)})
            elif op.type == A.ARM64_OP_MEM:
                ops.append({'kind': 'Memory', 'mem': {'base': reg_name(ins.reg_name(op.mem.base)),
                            'index': reg_name(ins.reg_name(op.mem.index)) if op.mem.index else None,
                            'disp': op.mem.disp}, 'ext': op.ext, 'shift': (op.shift.type, op.shift.value)})
            elif op.type == A.ARM64_OP_CIMM:
                ops.append({'kind': 'Immediate', 'imm': op.imm, 'size': 64, 'shift': (0, 0)})
            else:
                raise Unsupported('AArch64 operand in %s %s' % (ins.mnemonic, ins.op_str))
        mn = ins.mnemonic
        normalized = {'b': 'jmp', 'br': 'jmp', 'bl': 'call', 'blr': 'call',
                      'cbz': 'je', 'cbnz': 'jne', 'tbz': 'je', 'tbnz': 'jne'}.get(mn, mn)
        if mn.startswith('b.'):
            normalized = 'j' + mn[2:]
        j = {'offset': address, 'len': 4, 'mnemonic': normalized, 'a64': mn, 'ops': ops,
             'gas': mn + ' ' + ins.op_str, 'cc': ins.cc, 'writeback': ins.writeback,
             'post': ins.writeback and not ins.op_str.endswith('!'), 'flags': ins.update_flags}
        if mn == 'umov':
            # The lowest set bit of imm5 encodes log2(element bytes); the
            # remaining bits encode the lane. Capstone exposes the lane but
            # not its arrangement for this instruction.
            imm5 = (int.from_bytes(raw, 'little') >> 16) & 31
            if not imm5:
                raise Unsupported('invalid AArch64 UMOV element encoding')
            shift = (imm5 & -imm5).bit_length() - 1
            j['lane_bits'] = 8 << shift
            if shift > 3 or (imm5 >> (shift + 1)) != ops[1]['vector_index']:
                raise Unsupported('inconsistent AArch64 UMOV lane decoding')
        if mn in ('b', 'bl', 'cbz', 'cbnz', 'tbz', 'tbnz') or mn.startswith('b.'):
            j['branch_target'] = ops[-1]['imm']
        self.cache[address] = j
        return j


def branch_targets(image, entry=None):
    if entry is not None:
        targets, seen, todo = set(), set(), [entry]
        while todo:
            address = todo.pop()
            while address not in seen:
                seen.add(address)
                j = image.decode(address)
                if 'branch_target' in j:
                    targets.add(j['branch_target'])
                    todo.append(j['branch_target'])
                if j['a64'] in ('ret', 'b', 'br'):
                    break
                address += 4
        return targets
    targets = set()
    for name, start in image.symbols.items():
        size = image.sizes.get(name, 0)
        if not size or image.section_at(start) is not image.text:
            continue
        for address in range(start, start + size, 4):
            j = image.decode(address)
            if 'branch_target' in j:
                targets.add(j['branch_target'])
    return targets


def vector_width(name):
    return {'v': 16, 'q': 16, 'd': 8, 's': 4, 'h': 2, 'b': 1}.get(name[0]) if name[1:].isdigit() else None


def get(st, name):
    if name in ('xzr', 'wzr'):
        return const(64 if name == 'xzr' else 32, 0)
    if name in GPR:
        full, width, _ = GPR[name]
        return S.extract(st.gpr[full], width - 1, 0)
    width = vector_width(name)
    if width:
        return S.concat(*reversed(read_vec(st, 'zmm' + name[1:])[:width]))
    raise Unsupported('AArch64 register ' + name)


def put(st, name, value):
    if name in ('xzr', 'wzr'):
        return
    if name in GPR:
        full, width, _ = GPR[name]
        value = S.extract(value, width - 1, 0) if value.w > width else S.zext(value, width)
        st.gpr[full] = S.zext(value, 64)
        return
    width = vector_width(name)
    if width:
        value = S.extract(value, width * 8 - 1, 0) if value.w > width * 8 else S.zext(value, width * 8)
        lanes = [S.extract(value, i * 8 + 7, i * 8) for i in range(width)]
        st.vec[int(name[1:])] = lanes + [const(8, 0)] * (64 - width)
        return
    raise Unsupported('AArch64 register ' + name)


def shifted(value, shift):
    kind, amount = shift
    if kind == A.ARM64_SFT_INVALID:
        return value
    count = const(value.w, amount)
    if kind == A.ARM64_SFT_LSL:
        return S.shl(value, count)
    if kind == A.ARM64_SFT_LSR:
        return S.lshr(value, count)
    if kind == A.ARM64_SFT_ASR:
        return S.ashr(value, count)
    raise Unsupported('AArch64 shift %d' % kind)


def operand(st, op, width):
    value = const(width, op['imm']) if op['kind'] == 'Immediate' else get(st, op['reg'])
    ext = op.get('ext', A.ARM64_EXT_INVALID)
    if ext != A.ARM64_EXT_INVALID:
        unsigned = {A.ARM64_EXT_UXTB: 8, A.ARM64_EXT_UXTH: 16, A.ARM64_EXT_UXTW: 32, A.ARM64_EXT_UXTX: 64}
        signed = {A.ARM64_EXT_SXTB: 8, A.ARM64_EXT_SXTH: 16, A.ARM64_EXT_SXTW: 32, A.ARM64_EXT_SXTX: 64}
        bits = unsigned.get(ext, signed.get(ext))
        if bits is None:
            raise Unsupported('AArch64 extend %d' % ext)
        value = S.extract(value, bits - 1, 0)
        value = S.sext(value, width) if ext in signed else S.zext(value, width)
    value = S.extract(value, width - 1, 0) if value.w > width else S.zext(value, width)
    return shifted(value, op.get('shift', (0, 0)))


def condition(st, cc):
    def f(name):
        if name not in st.flags:
            raise Unsupported('read of undefined AArch64 flag ' + name)
        return st.flags[name]
    if cc == A.ARM64_CC_EQ: return f('Z')
    if cc == A.ARM64_CC_NE: return S.not_(f('Z'))
    if cc == A.ARM64_CC_HS: return f('C')
    if cc == A.ARM64_CC_LO: return S.not_(f('C'))
    if cc == A.ARM64_CC_MI: return f('N')
    if cc == A.ARM64_CC_PL: return S.not_(f('N'))
    if cc == A.ARM64_CC_VS: return f('V')
    if cc == A.ARM64_CC_VC: return S.not_(f('V'))
    if cc == A.ARM64_CC_HI: return S.and_(f('C'), S.not_(f('Z')))
    if cc == A.ARM64_CC_LS: return S.or_(S.not_(f('C')), f('Z'))
    if cc == A.ARM64_CC_GE: return S.eq(f('N'), f('V'))
    if cc == A.ARM64_CC_LT: return S.not_(S.eq(f('N'), f('V')))
    if cc == A.ARM64_CC_GT: return S.and_(S.not_(f('Z')), S.eq(f('N'), f('V')))
    if cc == A.ARM64_CC_LE: return S.or_(f('Z'), S.not_(S.eq(f('N'), f('V'))))
    if cc in (A.ARM64_CC_AL, A.ARM64_CC_NV): return S.TRUE
    raise Unsupported('AArch64 condition %d' % cc)


def flags(st, a, b, result, subtract):
    sign = lambda x: S.eq(S.extract(x, x.w - 1, x.w - 1), const(1, 1))
    st.flags = {'N': sign(result), 'Z': S.is_zero(result),
                'C': S.not_(S.ult(a, b)) if subtract else S.ult(result, a),
                'V': S.and_(S.not_(S.eq(sign(a), sign(b))) if subtract else S.eq(sign(a), sign(b)),
                            S.not_(S.eq(sign(a), sign(result))))}


def call_return(st, result, prefix):
    target = st.gpr['a30']
    if not S.is_const(target):
        raise Unsupported('AArch64 contract returning to a symbolic LR')
    st.fresh = prefix
    for name in NAMES[:19]:
        st.gpr[name] = var(prefix + name, 64)
    for n in range(32):
        old = read_vec(st, 'zmm%d' % n)
        new = [var('%szmm%d_%d' % (prefix, n, i), 8) for i in range(64)]
        st.vec[n] = old[:8] + new[8:] if 8 <= n < 16 else new
    st.flags = {}
    st.k = {}
    st.gpr['rax'] = result
    st.gpr['a30'] = var(prefix + 'a30', 64)
    st.rip = target.val


class Machine:
    def __init__(self, image, bmi=True):
        self.image, self.decide, self.targets = image, None, set()

    def step(self, st):
        j = self.image.decode(st.rip)
        st.steps += 1
        mn, ops = j['a64'], j['ops']
        nxt = st.rip + 4
        if aarch64_vectors.execute(self, st, j):
            pass
        elif mn in ('nop', 'bti'):
            pass
        elif mn == 'ret':
            target = get(st, ops[0]['reg']) if ops else st.gpr['a30']
            if not S.is_const(target):
                raise Unsupported('AArch64 ret to a symbolic address')
            st.done = target.val == RET_MARK
            st.rip = target.val
            return [(st, S.TRUE)]
        elif mn in ('b', 'bl', 'br', 'blr'):
            target = const(64, j['branch_target']) if 'branch_target' in j else get(st, ops[0]['reg'])
            if not S.is_const(target):
                raise Unsupported('AArch64 branch to a symbolic address')
            if mn in ('bl', 'blr'):
                st.gpr['a30'] = const(64, nxt)
            st.rip = target.val
            return [(st, S.TRUE)]
        elif mn.startswith('b.') or mn in ('cbz', 'cbnz', 'tbz', 'tbnz'):
            if mn.startswith('b.'):
                pred = condition(st, j['cc'])
            else:
                value = get(st, ops[0]['reg'])
                if mn.startswith('tb'):
                    bit = ops[1]['imm']
                    value = S.extract(value, bit, bit)
                pred = S.is_zero(value)
                if mn in ('cbnz', 'tbnz'):
                    pred = S.not_(pred)
            other = st.copy()
            st.rip, other.rip = j['branch_target'], nxt
            return [(st, pred), (other, S.not_(pred))]
        elif mn.startswith(('ldr', 'str', 'ldur', 'stur')) or mn in ('ldp', 'stp', 'ldpsw', 'ld1', 'st1'):
            memory = next(i for i, op in enumerate(ops) if op['kind'] == 'Memory')
            mem = ops[memory]
            if j['writeback'] and any(op['reg'] == mem['mem']['base'] for op in ops[:memory]):
                raise Unsupported('AArch64 writeback aliases its transferred register')
            address = S.add(get(st, mem['mem']['base']), const(64, mem['mem']['disp']))
            if mem['mem']['index']:
                idx = dict(mem, kind='Register', reg=mem['mem']['index'])
                address = S.add(address, operand(st, idx, 64))
            position = address
            for op in ops[:memory]:
                name = op['reg']
                width = vector_width(name) or (8 if name.startswith('x') else 4)
                if mn.endswith(('rb', 'rsb', 'urb', 'ursb')): width = 1
                elif mn.endswith(('rh', 'rsh', 'urh', 'ursh')): width = 2
                elif mn in ('ldrsw', 'ldursw', 'ldpsw'): width = 4
                if mn.startswith('ld'):
                    lanes = B.load(self, st, position, width, st.rip)
                    value = S.concat(*reversed(lanes))
                    if mn in ('ldrsb', 'ldrsh', 'ldrsw', 'ldursb', 'ldursh', 'ldursw', 'ldpsw'):
                        value = S.sext(value, 64 if name.startswith('x') else 32)
                    put(st, name, value)
                else:
                    value = get(st, name)
                    B.store_bytes(self, st, position, [S.extract(value, i * 8 + 7, i * 8) for i in range(width)], st.rip)
                position = S.add(position, const(64, width))
            if j['writeback']:
                base = mem['mem']['base']
                updated = S.add(get(st, base), const(64, ops[-1]['imm'])) if j['post'] else address
                put(st, base, updated)
        elif mn == 'umov':
            value = get(st, ops[1]['reg'])
            bits = j['lane_bits']
            if bits > get(st, ops[0]['reg']).w or ops[1]['vector_index'] < 0:
                raise Unsupported('AArch64 lane layout in ' + j['gas'])
            start = bits * ops[1]['vector_index']
            put(st, ops[0]['reg'], S.extract(value, start + bits - 1, start))
        elif mn in ('dup', 'movi', 'cmeq', 'uminv', 'umaxv', 'orr', 'and', 'eor') and vector_width(ops[0]['reg']):
            name = ops[0]['reg']
            arrangement = ops[1]['vas'] if mn in ('uminv', 'umaxv') else ops[0]['vas']
            if arrangement != A.ARM64_VAS_16B:
                raise Unsupported('AArch64 non-byte vector layout in ' + j['gas'])
            if mn in ('dup', 'movi'):
                # This first backend supports byte lanes only; other layouts fail closed.
                if ops[0].get('vas') not in (A.ARM64_VAS_16B, A.ARM64_VAS_8B):
                    raise Unsupported('AArch64 vector layout in ' + j['gas'])
                value = operand(st, ops[1], 8)
                put(st, name, S.concat(*([value] * vector_width(name))))
            else:
                value = get(st, ops[1]['reg'])
                lanes = [S.extract(value, i * 8 + 7, i * 8) for i in range(value.w // 8)]
                if mn in ('uminv', 'umaxv'):
                    result = lanes[0]
                    for lane in lanes[1:]:
                        pred = S.ult(lane, result) if mn == 'uminv' else S.ult(result, lane)
                        result = S.ite(pred, lane, result)
                    put(st, name, result)
                else:
                    other = operand(st, ops[2], value.w)
                    if mn == 'cmeq':
                        out = [S.ite(S.eq(lane, S.extract(other, i * 8 + 7, i * 8)), const(8, 255), const(8, 0)) for i, lane in enumerate(lanes)]
                        result = S.concat(*reversed(out))
                    else:
                        result = {'orr': S.bor, 'and': S.band, 'eor': S.bxor}[mn](value, other)
                    put(st, name, result)
        elif mn in ('mov', 'movz', 'movn', 'movk', 'adr', 'adrp', 'fmov'):
            name = ops[0]['reg']
            width = get(st, name).w
            value = operand(st, ops[1], width)
            if mn == 'movn': value = S.bnot(value)
            if mn == 'movk':
                shift = ops[1]['shift'][1]
                mask = ((1 << 16) - 1) << shift
                value = S.bor(S.band(get(st, name), const(width, ~mask)), value)
            put(st, name, value)
        elif mn in ('add', 'adds', 'sub', 'subs', 'cmp', 'cmn', 'and', 'ands', 'tst', 'orr', 'eor', 'bic'):
            compare = mn in ('cmp', 'cmn', 'tst')
            a = get(st, ops[0 if compare else 1]['reg'])
            b = operand(st, ops[1 if compare else 2], a.w)
            if mn in ('sub', 'subs', 'cmp'): result = S.sub(a, b)
            elif mn in ('add', 'adds', 'cmn'): result = S.add(a, b)
            elif mn in ('and', 'ands', 'tst', 'bic'): result = S.band(a, S.bnot(b) if mn == 'bic' else b)
            elif mn == 'orr': result = S.bor(a, b)
            else: result = S.bxor(a, b)
            if mn in ('adds', 'subs', 'cmp', 'cmn'):
                flags(st, a, b, result, mn in ('subs', 'cmp'))
            elif mn in ('ands', 'tst'):
                st.flags = {'N': S.eq(S.extract(result, result.w - 1, result.w - 1), const(1, 1)),
                            'Z': S.is_zero(result), 'C': S.FALSE, 'V': S.FALSE}
            if not compare: put(st, ops[0]['reg'], result)
        elif mn in ('rev', 'rbit', 'clz'):
            value = get(st, ops[1]['reg'])
            if mn == 'rev':
                result = S.concat(*[S.extract(value, i + 7, i) for i in range(0, value.w, 8)])
            elif mn == 'rbit':
                result = S.concat(*[S.extract(value, i, i) for i in range(value.w)])
            else:
                result = const(value.w, value.w)
                for i in range(value.w):
                    result = S.ite(S.eq(S.extract(value, i, i), const(1, 1)), const(value.w, value.w - 1 - i), result)
            put(st, ops[0]['reg'], result)
        elif mn in ('ubfiz', 'bfi'):
            value = get(st, ops[1]['reg'])
            low, bits = ops[2]['imm'], ops[3]['imm']
            if not 0 <= low < value.w or not 1 <= bits <= value.w - low:
                raise Unsupported('AArch64 bitfield in ' + j['gas'])
            mask = ((1 << bits) - 1) << low
            result = S.shl(S.zext(S.extract(value, bits - 1, 0), value.w), const(value.w, low))
            if mn == 'bfi':
                result = S.bor(S.band(get(st, ops[0]['reg']), const(value.w, ~mask)), result)
            put(st, ops[0]['reg'], result)
        elif mn == 'mul':
            a = get(st, ops[1]['reg'])
            put(st, ops[0]['reg'], S.mul(a, operand(st, ops[2], a.w)))
        elif mn == 'mvn':
            value = get(st, ops[1]['reg'])
            put(st, ops[0]['reg'], S.bnot(operand(st, ops[1], value.w)))
        elif mn in ('lsl', 'lsr', 'asr'):
            value = get(st, ops[1]['reg'])
            amount = S.band(operand(st, ops[2], value.w), const(value.w, value.w - 1))
            put(st, ops[0]['reg'], {'lsl': S.shl, 'lsr': S.lshr, 'asr': S.ashr}[mn](value, amount))
        elif mn in ('uxtb', 'uxth', 'uxtw', 'sxtb', 'sxth', 'sxtw'):
            value = get(st, ops[1]['reg'])
            bits = {'b': 8, 'h': 16, 'w': 32}[mn[-1]]
            value = S.extract(value, bits - 1, 0)
            width = get(st, ops[0]['reg']).w
            put(st, ops[0]['reg'], S.sext(value, width) if mn[0] == 's' else S.zext(value, width))
        elif mn in ('csel', 'csinc', 'csinv', 'csneg', 'cset', 'csetm'):
            pred = condition(st, j['cc'])
            width = get(st, ops[0]['reg']).w
            if mn in ('cset', 'csetm'):
                yes, no = const(width, -1 if mn == 'csetm' else 1), const(width, 0)
            else:
                yes, no = get(st, ops[1]['reg']), get(st, ops[2]['reg'])
                if mn == 'csinc': no = S.add(no, const(width, 1))
                elif mn == 'csinv': no = S.bnot(no)
                elif mn == 'csneg': no = S.sub(const(width, 0), no)
            put(st, ops[0]['reg'], S.ite(pred, yes, no))
        else:
            raise Unsupported('%s at %#x' % (j['gas'], st.rip))
        st.rip = nxt
        return [(st, S.TRUE)]
