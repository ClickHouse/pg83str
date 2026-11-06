"""Explicit NEON and fixed-VL SVE semantics used by the ARM proof machine.

SVE execution requires an explicit VL assumption. Only the measured 16-byte
VL is currently modeled. Unsupported arrangements and predication forms
raise rather than receiving approximate semantics.
"""
from capstone import arm64 as A
import machine as B
import smt as S
from smt import const, var


def unpack(value, bits=8):
    return [S.extract(value, i + bits - 1, i) for i in range(0, value.w, bits)]


def pack(values):
    return S.concat(*reversed(values))


def pred_flags(st, active, result):
    first = last = S.FALSE
    for a, r in reversed(list(zip(active, result))):
        first = S.ite(a, r, first)
    for a, r in zip(active, result):
        last = S.ite(a, r, last)
    st.flags = {'N': first, 'Z': S.not_(S.or_(*(S.and_(a, r) for a, r in zip(active, result)))),
                'C': S.not_(last), 'V': S.FALSE}


def execute(machine, st, j):
    import aarch64_machine as M
    mn, ops = j['a64'], j['ops']
    name = ops[0].get('reg', '') if ops else ''
    sve = mn in ('rdvl', 'whilelo', 'ld1b', 'st1b', 'cmpeq', 'brka', 'brkb', 'ptrue', 'cntp', 'cnth', 'incp') or name.startswith('z')
    if sve:
        vl = machine.image.sve_vl
        if vl != 16:
            raise B.Unsupported('SVE requires explicit --sve-vl 16: ' + j['gas'])

        def predicate(op):
            n = op['reg']
            if not n.startswith('p') or not n[1:].isdigit():
                raise B.Unsupported('SVE predicate operand in ' + j['gas'])
            p = st.k.setdefault(n, var(st.fresh + n, vl))
            return [S.eq(S.extract(p, i, i), const(1, 1)) for i in range(vl)]

        def put_pred(values):
            st.k[name] = B.bits(values)

        def zlanes(op):
            n = op['reg']
            if not n.startswith('z') or not n[1:].isdigit():
                raise B.Unsupported('SVE vector operand in ' + j['gas'])
            return B.read_vec(st, 'zmm' + n[1:])[:vl]

        def put_z(values):
            st.vec[int(name[1:])] = list(values) + [const(8, 0)] * (64 - vl)

        if mn == 'rdvl':
            M.put(st, name, const(64, vl * ops[1]['imm']))
        elif mn == 'cnth':
            if ', all' not in j['gas']:
                raise B.Unsupported('SVE count pattern in ' + j['gas'])
            M.put(st, name, const(64, vl // 2 * (ops[1]['imm'] if len(ops) > 1 else 1)))
        elif mn == 'ptrue':
            if j['gas'] != 'ptrue ' + name + '.b':
                raise B.Unsupported('SVE predicate pattern in ' + j['gas'])
            put_pred([S.TRUE] * vl)
        elif mn == 'whilelo':
            if not j['gas'].startswith('whilelo ' + name + '.b,'):
                raise B.Unsupported('SVE element size in ' + j['gas'])
            a, b = M.get(st, ops[1]['reg']), M.get(st, ops[2]['reg'])
            result = []
            for i in range(vl):
                value = S.add(a, const(a.w, i))
                result.append(S.and_(S.ule(a, value), S.ult(value, b)))
            put_pred(result)
            pred_flags(st, [S.TRUE] * vl, result)
        elif mn in ('ld1b', 'st1b'):
            if ops[0]['vas'] != A.ARM64_VAS_1B or (mn == 'ld1b' and '/z' not in j['gas']):
                raise B.Unsupported('SVE load/store layout in ' + j['gas'])
            active = predicate(ops[1])
            mem = ops[2]
            if 'mul vl' in j['gas']:
                raise B.Unsupported('SVE vector-scaled memory offset in ' + j['gas'])
            address = S.add(M.get(st, mem['mem']['base']), const(64, mem['mem']['disp']))
            if mem['mem']['index']:
                address = S.add(address, M.operand(st, dict(mem, kind='Register', reg=mem['mem']['index']), 64))
            if mn == 'ld1b':
                values = []
                for i, enabled in enumerate(active):
                    if enabled is S.FALSE:
                        values.append(const(8, 0))
                    else:
                        byte = B.load(machine, st, S.add(address, const(64, i)), 1, st.rip, [enabled])[0]
                        values.append(S.ite(enabled, byte, const(8, 0)))
                put_z(values)
            else:
                B.store_bytes(machine, st, address, zlanes(ops[0]), st.rip, active)
        elif mn == 'cmpeq':
            if not j['gas'].startswith('cmpeq ' + name + '.b,') or '/z' not in j['gas']:
                raise B.Unsupported('SVE compare layout in ' + j['gas'])
            active, a = predicate(ops[1]), zlanes(ops[2])
            b = [const(8, ops[3]['imm'])] * vl if ops[3]['kind'] == 'Immediate' else zlanes(ops[3])
            result = [S.and_(p, S.eq(x, y)) for p, x, y in zip(active, a, b)]
            put_pred(result)
            pred_flags(st, active, result)
        elif mn in ('brka', 'brkb'):
            if not j['gas'].startswith(mn + ' ' + name + '.b,') or '/z' not in j['gas']:
                raise B.Unsupported('SVE break layout in ' + j['gas'])
            active, source = predicate(ops[1]), predicate(ops[2])
            stopped, result = S.FALSE, []
            for enabled, value in zip(active, source):
                hit = S.and_(enabled, value)
                if mn == 'brkb':
                    stopped = S.or_(stopped, hit)
                result.append(S.and_(enabled, S.not_(stopped)))
                if mn == 'brka':
                    stopped = S.or_(stopped, hit)
            put_pred(result)
        elif mn in ('cntp', 'incp'):
            if not j['gas'].endswith('.b'):
                raise B.Unsupported('SVE count element size in ' + j['gas'])
            active = predicate(ops[1])
            values = [S.and_(x, y) for x, y in zip(active, predicate(ops[2]))] if mn == 'cntp' else active
            value = const(64, 0) if mn == 'cntp' else M.get(st, name)
            for bit in values:
                value = S.add(value, S.ite(bit, const(64, 1), const(64, 0)))
            M.put(st, name, value)
        elif mn == 'mov' and name.startswith('z'):
            if ops[0]['vas'] != A.ARM64_VAS_1B or ops[1]['reg'][0] not in ('w', 'x'):
                raise B.Unsupported('SVE broadcast in ' + j['gas'])
            put_z([M.operand(st, ops[1], 8)] * vl)
        else:
            raise B.Unsupported('SVE semantics for ' + j['gas'])
        return True

    if not name or not M.vector_width(name):
        return False
    supported = ('mvn', 'orn', 'bic', 'orr', 'and', 'eor', 'cmeq', 'cmlt', 'addp', 'umin', 'uminv', 'umaxv',
                 'shrn', 'shl', 'ushl', 'usra', 'tbl', 'dup', 'movi')
    if mn not in supported:
        return False
    if mn == 'shrn':
        if ops[0]['vas'] != A.ARM64_VAS_8B or ops[1]['vas'] != A.ARM64_VAS_8H or not 1 <= ops[2]['imm'] <= 8:
            raise B.Unsupported('NEON narrow layout in ' + j['gas'])
        values = [S.extract(S.lshr(v, const(16, ops[2]['imm'])), 7, 0) for v in unpack(M.get(st, ops[1]['reg']), 16)]
        M.put(st, name, pack(values))
        return True
    arrangement = ops[1]['vas'] if mn in ('uminv', 'umaxv') else ops[0]['vas']
    if mn == 'movi' and arrangement == A.ARM64_VAS_2D and ops[1]['imm'] == 0:
        M.put(st, name, const(128, 0))
        return True
    if arrangement not in (A.ARM64_VAS_16B, A.ARM64_VAS_8B):
        raise B.Unsupported('NEON non-byte layout in ' + j['gas'])
    width = 128 if arrangement == A.ARM64_VAS_16B else 64

    def value(op):
        return M.operand(st, op, width)

    if mn in ('dup', 'movi'):
        result = pack([M.operand(st, ops[1], 8)] * (width // 8))
    else:
        a = value(ops[1])
        av = unpack(a)
        if mn in ('uminv', 'umaxv'):
            result = av[0]
            for v in av[1:]:
                test = S.ult(v, result) if mn == 'uminv' else S.ult(result, v)
                result = S.ite(test, v, result)
        elif mn == 'mvn':
            result = S.bnot(a)
        elif mn == 'tbl':
            table = sum((unpack(value(op)) for op in ops[1:-1]), [])
            result = []
            for index in unpack(value(ops[-1])):
                byte = const(8, 0)
                for i, v in reversed(list(enumerate(table))):
                    byte = S.ite(S.eq(index, const(8, i)), v, byte)
                result.append(byte)
            result = pack(result)
        elif mn in ('shl', 'usra'):
            if not 0 <= ops[2]['imm'] <= 8:
                raise B.Unsupported('NEON shift in ' + j['gas'])
            count = const(8, ops[2]['imm'])
            values = [S.shl(v, count) if mn == 'shl' else S.add(old, S.lshr(v, count))
                      for v, old in zip(av, unpack(value(ops[0])))]
            result = pack(values)
        else:
            b = value(ops[2])
            bv = unpack(b)
            if mn in ('and', 'orr', 'eor', 'bic', 'orn'):
                result = {'and': S.band, 'orr': S.bor, 'eor': S.bxor,
                          'bic': lambda x, y: S.band(x, S.bnot(y)), 'orn': lambda x, y: S.bor(x, S.bnot(y))}[mn](a, b)
            elif mn == 'addp':
                result = pack([S.add(v[i], v[i + 1]) for v in (av, bv) for i in range(0, len(v), 2)])
            elif mn in ('cmeq', 'cmlt'):
                test = S.eq if mn == 'cmeq' else S.slt
                result = pack([S.ite(test(x, y), const(8, 255), const(8, 0)) for x, y in zip(av, bv)])
            elif mn == 'umin':
                result = pack([S.ite(S.ult(x, y), x, y) for x, y in zip(av, bv)])
            elif mn == 'ushl':
                result = pack([S.ite(S.slt(y, const(8, 0)), S.lshr(x, S.sub(const(8, 0), y)), S.shl(x, y)) for x, y in zip(av, bv)])
            else:
                raise B.Unsupported('NEON semantics for ' + j['gas'])
    M.put(st, name, result)
    return True
