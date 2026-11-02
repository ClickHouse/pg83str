"""Emit an object back as GAS assembly: every code section from its (possibly
rewritten) decoded instructions, data sections as bytes with symbolic
relocations, symbols with their binding, type and size. Round-tripping an
unmodified object must give the same instruction sequence."""
import os


def sec_tag(name):
    return name.strip('.').replace('.', '_')


def label(section, offset):
    return '.L_%x' % offset if section == '.text' else '.L_%s_%x' % (sec_tag(section), offset)


def section_directive(sec):
    if sec.name == '.text':
        return '.text'
    if sec.name == '.bss':
        return '.bss'
    flags = ''
    if sec.flags & 0x2: flags += 'a'
    if sec.flags & 0x1: flags += 'w'
    if sec.flags & 0x4: flags += 'x'
    kind = {'SHT_NOBITS': '@nobits', 'SHT_INIT_ARRAY': '@init_array', 'SHT_FINI_ARRAY': '@fini_array'}.get(sec.type, '@progbits')
    return '.section %s,"%s",%s' % (sec.name, flags, kind)


def reloc_symbol(obj, r, adj):
    """Symbolic operand for a relocation: sym+adj, with section symbols mapped to a label."""
    total = r.addend + adj
    if r.symbol_is_section:
        if obj.sections[r.symbol].is_code:
            return label(r.symbol, total)
        return '%s+%d' % ('.Lsec_' + sec_tag(r.symbol), total)
    return r.symbol if total == 0 else '%s%+d' % (r.symbol, total)


def instruction_text(obj, section, i):
    """GAS text for one instruction with labels/symbols in place of numbers."""
    text = i.gas
    r = i.reloc
    if r is not None:
        pos = r.offset - i.home
        op = next((o for o in i.j['ops'] if o.get('field_pos') == pos), None)
        if op is None:
            raise ValueError('relocation %s at %d does not match an operand of %s' % (r.type, pos, i))
        literal = op['literal']
        if r.type in ('R_X86_64_PLT32', 'R_X86_64_PC32'):
            adj = i.home_end - r.offset  # the field is relative to the end of the instruction
            symbolic = reloc_symbol(obj, r, adj)
            if op['kind'] == 'NearBranch':
                return '%s %s' % (i.mnemonic, symbolic)
            if op['kind'] == 'Memory' and op['mem']['rip_rel']:
                return text.replace(literal, symbolic + '(%rip)', 1)
            raise ValueError('%s on %s operand of %s' % (r.type, op['kind'], i))
        if r.type in ('R_X86_64_32', 'R_X86_64_32S', 'R_X86_64_64'):
            symbolic = reloc_symbol(obj, r, 0)
            if op['kind'] == 'Immediate':
                return text.replace('$' + literal, '$' + symbolic, 1)
            if op['kind'] == 'Memory':
                if literal == '':
                    k = text.index('(')  # a zero displacement prints as nothing: insert before the parenthesis
                    return text[:k] + symbolic + text[k:]
                return text.replace(literal, symbolic, 1)
        raise ValueError('relocation %s on instruction %s' % (r.type, i))
    if i.target is not None:
        literal = '0x%016X' % i.target
        assert literal in text, (text, literal)
        return text.replace(literal, label(section, i.target))
    return text


def emit(obj, code, path):
    """code: {section name: decoded instructions (after rewriting), in order}."""
    out = ['/* generated from %s by post/emit.py */' % obj.path.name]
    symbols_by_section = {}
    for s in obj.symbols:
        if s.type in ('STT_FUNC', 'STT_OBJECT', 'STT_NOTYPE') and s.name and s.section in obj.sections:
            symbols_by_section.setdefault(s.section, []).append(s)
    # Offsets referenced from data (function tables, init_array) need labels in their code section.
    data_targets = {}
    for sec in obj.sections.values():
        if sec.name in code:
            continue
        for r in sec.relocs:
            if r.symbol_is_section and obj.sections[r.symbol].is_code:
                data_targets.setdefault(r.symbol, set()).add(r.addend)
    for instructions in code.values():  # cross-section jumps/calls through a section symbol (gcc's .cold parts)
        for i in instructions:
            r = i.reloc
            if r is not None and r.symbol_is_section and obj.sections[r.symbol].is_code and r.type in ('R_X86_64_PC32', 'R_X86_64_PLT32'):
                data_targets.setdefault(r.symbol, set()).add(r.addend + i.home_end - r.offset)
    for name, instructions in code.items():
        sec = obj.sections[name]
        covered = set()
        for f in obj.functions(name):
            covered.update(range(f.value, f.value + f.size))
        targets = {i.target for i in instructions if i.target is not None and i.reloc is None} | data_targets.get(name, set())
        starts = {}
        for s in symbols_by_section.get(name, []):
            starts.setdefault(s.value, []).append(s)
        # Alignment padding: a run of nops ending on an 8-byte boundary at a
        # branch target, a symbol or outside any function. It becomes one
        # .p2align at the run's end (keeps the alignment, not the bytes); any
        # other nop is a real instruction (gcc -O0 emits them) and stays.
        padding = {}
        run = []

        def alignment(run):
            # The padding is shorter than the alignment it reaches; assume at least 16 (the usual
            # loop/function alignment) and never more than the target offset supports.
            length = run[-1].end - run[0].offset
            k = max(length.bit_length(), 4)
            return min(k, (run[-1].end & -run[-1].end).bit_length() - 1, 6)

        def flush(run):
            if run and run[-1].end % 8 == 0 and (run[-1].end in targets or run[-1].end in starts or run[-1].end not in covered):
                for n in run:
                    padding[n.offset] = None
                padding[run[-1].offset] = alignment(run)
            elif run and run[-1].end not in covered:
                for n in run:
                    padding[n.offset] = None
        for i in instructions:
            if i.mnemonic == 'nop' and i.gas:
                run.append(i)
                continue
            flush(run)
            run = []
        flush(run)
        out.append(section_directive(sec))
        out.append(label(name, 0) + ':')
        func_align = os.environ.get('POST_FUNC_ALIGN')  # A/B runs: same function alignment for both copies
        for i in instructions:
            if func_align and any(s.type == 'STT_FUNC' for s in starts.get(i.offset, [])):
                out.append('\t.p2align ' + func_align)
            for s in starts.get(i.offset, []):
                if s.bind == 'STB_GLOBAL':
                    out.append('.globl ' + s.name)
                elif s.bind == 'STB_WEAK':
                    out.append('.weak ' + s.name)
                out.append('.type %s,@%s' % (s.name, 'function' if s.type == 'STT_FUNC' else 'object'))
                out.append(s.name + ':')
            if i.offset in targets and i.offset != 0:
                out.append(label(name, i.offset) + ':')
            if i.offset in padding:
                if padding[i.offset] is not None:
                    out.append('\t.p2align %d' % padding[i.offset])
                continue
            if i.home not in covered or not i.gas:
                continue  # stray padding, or deleted by a pass (its label, if any, now names the next instruction)
            out.append('\t' + instruction_text(obj, name, i))
        for s in symbols_by_section.get(name, []):
            out.append('.size %s, %d' % (s.name, s.size))
    for name, sec in obj.sections.items():
        if name in code or name in ('.comment', '.note.GNU-stack', '.eh_frame') or name.startswith('.llvm'):
            continue
        out.append(section_directive(sec))
        if sec.align > 1:
            out.append('.p2align %d' % (sec.align.bit_length() - 1))
        out.append('.Lsec_%s:' % sec_tag(name))
        syms = {}
        for s in symbols_by_section.get(name, []):
            syms.setdefault(s.value, []).append(s)
        relocs = {r.offset: r for r in sec.relocs}
        size = sec.nobits_size if sec.type == 'SHT_NOBITS' else len(sec.data)
        pos = 0
        while pos < size:
            for s in syms.get(pos, []):
                if s.bind == 'STB_GLOBAL':
                    out.append('.globl ' + s.name)
                elif s.bind == 'STB_WEAK':
                    out.append('.weak ' + s.name)
                out.append('.type %s,@object' % s.name)
                out.append(s.name + ':')
            if sec.type == 'SHT_NOBITS':
                nxt = min([v for v in syms if v > pos] + [size])
                out.append('.zero %d' % (nxt - pos))
                pos = nxt
                continue
            if pos in relocs:
                r = relocs[pos]
                if r.type == 'R_X86_64_64':
                    out.append('.quad ' + reloc_symbol(obj, r, 0))
                    pos += 8
                    continue
                if r.type in ('R_X86_64_32', 'R_X86_64_32S'):
                    out.append('.long ' + reloc_symbol(obj, r, 0))
                    pos += 4
                    continue
                if r.type == 'R_X86_64_PC32':
                    out.append('.long %s-.' % reloc_symbol(obj, r, 0))
                    pos += 4
                    continue
                raise ValueError('data relocation ' + r.type)
            nxt = min([v for v in syms if v > pos] + [o for o in relocs if o > pos] + [size])
            chunk = sec.data[pos:nxt]
            for k in range(0, len(chunk), 16):
                out.append('.byte ' + ','.join('0x%02x' % b for b in chunk[k:k + 16]))
            pos = nxt
        for s in symbols_by_section.get(name, []):
            out.append('.size %s, %d' % (s.name, s.size))
    out.append('.section .note.GNU-stack,"",@progbits')
    path.write_text('\n'.join(out) + '\n')
