"""Regenerate x86-64 unwind rows after layout; unsupported inputs fail closed.

Compiler CFI is the source of saved-register rules. A transformed instruction
may carry a rule transition only when its frame effect is unchanged. The final
CFG, rather than the original linear PC order, propagates those transitions.
"""
from bisect import bisect_right
from dataclasses import dataclass
import struct

import elf
import x86


def need(condition, message):
    if not condition:
        raise ValueError('unwind: ' + message)


def leb(data, pos, signed=False):
    value = shift = 0
    while True:
        need(pos < len(data) and shift < 70, 'truncated/oversized LEB128')
        byte = data[pos]; pos += 1
        value |= (byte & 127) << shift; shift += 7
        if not byte & 128:
            if signed and byte & 64:
                value -= 1 << shift
            return value, pos


def uleb(value):
    need(value >= 0, 'negative ULEB128')
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128); value >>= 7
    out.append(value)
    return bytes(out)


@dataclass(frozen=True)
class State:
    cfa: tuple = (7, 8)
    rules: tuple = ((16, 'offset', -8),)

    def __post_init__(self):
        object.__setattr__(self, 'rules', tuple(r for r in self.rules if r[1] != 'same'))

    def rule(self, reg, kind, value=0):
        rules = {r[0]:r for r in self.rules}
        rules[reg] = (reg, kind, value)
        return State(self.cfa, tuple(sorted(rules.values())))


def program(data, initial, code_align=1, data_align=-8):
    """Decode complete CFI states, including remember/restore; no opaque opcodes."""
    state = initial; rows = {0:state}; pos = pc = 0; stack = []
    while pos < len(data):
        op = data[pos]; pos += 1
        if op & 0xc0 == 0x40:
            pc += (op & 63) * code_align
        elif op & 0xc0 == 0x80:
            value,pos = leb(data,pos); state = state.rule(op & 63,'offset',value*data_align)
        elif op & 0xc0 == 0xc0:
            reg = op & 63; old = next((r for r in initial.rules if r[0]==reg), (reg,'same',0))
            state = state.rule(*old)
        elif op == 0:
            continue
        elif op in (2,3,4):
            width = {2:1,3:2,4:4}[op]; need(pos+width<=len(data),'truncated advance')
            pc += int.from_bytes(data[pos:pos+width],'little')*code_align; pos += width
        elif op in (5,17,20,21):
            reg,pos = leb(data,pos); value,pos = leb(data,pos,op in (17,21))
            state = state.rule(reg,'offset' if op in (5,17) else 'val_offset',value*data_align)
        elif op == 6:
            reg,pos = leb(data,pos); old = next((r for r in initial.rules if r[0]==reg),(reg,'same',0)); state=state.rule(*old)
        elif op in (7,8):
            reg,pos = leb(data,pos); state = state.rule(reg,'undefined' if op==7 else 'same')
        elif op == 9:
            reg,pos=leb(data,pos);value,pos=leb(data,pos);state=state.rule(reg,'register',value)
        elif op == 10:
            stack.append(state)
        elif op == 11:
            need(bool(stack),'restore_state without remember');state=stack.pop()
        elif op in (12,18):
            reg,pos=leb(data,pos);value,pos=leb(data,pos,op==18);state=State((reg,value*data_align if op==18 else value),state.rules)
        elif op == 13:
            reg,pos=leb(data,pos);state=State((reg,state.cfa[1]),state.rules)
        elif op in (14,19):
            value,pos=leb(data,pos,op==19);state=State((state.cfa[0],value*data_align if op==19 else value),state.rules)
        else:
            raise ValueError('unwind: unsupported CFI opcode %#x' % op)
        rows[pc] = state
    need(not stack,'unbalanced CFI remember_state')
    return rows


@dataclass
class Frame:
    section: str
    start: int
    size: int
    rows: dict
    initial: State

    def at(self, pc):
        keys = sorted(self.rows)
        need(self.start <= pc <= self.start+self.size, 'PC outside source FDE')
        return self.rows[keys[bisect_right(keys,pc-self.start)-1]]


def read_frames(path):
    e=elf.read(path); sections={s.name:s for s in e.sections}
    need('.eh_frame' in sections,'compiler object has no .eh_frame')
    sec=sections['.eh_frame'];data=sec.data;relocs={r.offset:r for r in e.relocs.get(sec.index,[])}
    cies={};frames=[];pos=0
    while pos<len(data):
        begin=pos;need(pos+4<=len(data),'truncated CFI record');length=struct.unpack_from('<I',data,pos)[0];pos+=4
        if length==0:
            need(not any(data[pos:]),'bytes after terminator');break
        need(length!=0xffffffff and length>=4 and pos+length<=len(data),'unsupported CFI record length')
        end=pos+length;reference=struct.unpack_from('<I',data,pos)[0];pos+=4
        if reference==0:
            need(data[pos]==1,'only CIE v1 supported');pos+=1
            z=data.index(0,pos,end);augmentation=data[pos:z].decode();pos=z+1
            need(augmentation=='zR','unsupported CIE augmentation '+augmentation)
            ca,pos=leb(data,pos);da,pos=leb(data,pos,True);ra=data[pos];pos+=1
            need(ca==1 and da==-8 and ra==16,'unsupported AMD64 CIE factors')
            n,pos=leb(data,pos);need(n==1 and data[pos]==0x1b,'expected pcrel sdata4 FDE encoding');pos+=n
            initial=program(data[pos:end],State((0,0),()),ca,da)
            need(list(initial)==[0],'CIE has PC advances')
            cies[begin]=initial[0]
        else:
            cie=begin+4-reference;need(cie in cies,'unknown CIE')
            need(pos in relocs,'FDE start lacks relocation');r=relocs.pop(pos)
            need(r.type=='R_X86_64_PC32','unsupported FDE relocation')
            sym=e.symbols[r.symbol];section=e.section_name(sym.shndx)
            need(section in sections and sections[section].flags & 4,'FDE does not reference code')
            start=sym.value+r.addend;size=struct.unpack_from('<I',data,pos+4)[0];pos+=8
            n,pos=leb(data,pos);need(n==0,'FDE augmentation unsupported')
            rows=program(data[pos:end],cies[cie]);need(max(rows)<=size,'CFI extends beyond function')
            for state in rows.values():
                need(state.cfa[0] in (6,7),'unsupported CFA base register')
                need(all(r[0] in (3,6,12,13,14,15,16) for r in state.rules),'unsupported saved-register column')
                need(all(r[1]=='offset' for r in state.rules),'unsupported saved-register rule (only CFA offsets and same-value supported)')
            frames.append(Frame(section,start,size,rows,cies[cie]))
        pos=end
    need(not relocs,'unhandled .eh_frame relocation')
    return frames


def state_program(state, regs):
    """A full independent row: no remember/restore or inherited missing rule."""
    out=b'\x0c'+uleb(state.cfa[0])+uleb(state.cfa[1]);rules={r[0]:r[1:] for r in state.rules}
    for reg in sorted(regs):
        kind,value=rules.get(reg,('same',0))
        if kind=='offset':
            need(value<=0 and value%8==0,'unsupported offset rule')
            out+=b'\x05'+uleb(reg)+uleb(-value//8)
        elif kind in ('same','undefined'):
            out+=bytes([8 if kind=='same' else 7])+uleb(reg)
        elif kind=='register':
            out+=b'\x09'+uleb(reg)+uleb(value)
        else:
            raise ValueError('unwind: unsupported output rule '+kind)
    return out


def encode_frames(frames):
    """Return regenerated .eh_frame and relocations against final code sections."""
    need(bool(frames),'no final FDEs')
    initial=State();cie=b'\0\0\0\0\x01zR\0\x01\x78\x10\x01\x1b'+state_program(initial,{16})
    cie+=b'\0'*(-len(cie)%4);out=bytearray(struct.pack('<I',len(cie))+cie);relocs=[]
    for section,start,size,rows in frames:
        need(size>0 and 0 in rows,'empty/uninitialized final FDE')
        regs={16}|{r[0]for state in rows.values()for r in state.rules}
        pc=0;body=b''
        for where,state in sorted(rows.items()):
            need(0<=where<size,'final row outside FDE')
            if where!=pc:
                body+=b'\x04'+struct.pack('<I',where-pc);pc=where
            body+=state_program(state,regs)
        begin=len(out);record=struct.pack('<III',begin+4,0,size)+b'\0'+body
        record+=b'\0'*(-len(record)%4);out+=struct.pack('<I',len(record))+record
        relocs.append((begin+8,'R_X86_64_PC32',section,True,start))
    return bytes(out),relocs


CALLEE_SAVED = {'rbx','rbp','r12','r13','r14','r15'}
WRITES = ('Write','CondWrite','ReadWrite','ReadCondWrite')


def frame_sensitive(i):
    """Effects that must not be changed while reusing compiler unwind rules."""
    if i['flow'] in ('Call','IndirectCall','Return'):
        return False  # call returns to its fallthrough; ret has no successor
    if any(r['full'] in CALLEE_SAVED | {'rsp'} and r['access'] in WRITES for r in i['regs']):
        return True
    return any(m['base'] in ('rsp','rbp') and m['access'] in WRITES for m in i['mems'])


def verify_effect_traces(source, final, provenance, entry):
    """Every final frame-effect trace must be an original CFG trace.

    CFA can stay RBP-based while a push changes RSP. Comparing CFI rows alone
    would therefore miss some removed stack effects. Treat preserved frame
    instructions and exits as an alphabet; ordinary instructions are epsilon
    edges. Determinize the original CFG on each final path, retaining distinct
    frontiers at joins instead of unsoundly unioning different path histories.
    """
    exit_node = -1
    source_at = {i.offset:i for i in source}
    final_at = {i['offset']:i for i in final}

    def successors(i, relocated):
        pc = i['offset']; end = pc+i['len']; flow = i['flow']
        if flow in ('Return','Exception','IndirectBranch'):
            return [exit_node]
        if flow in ('UnconditionalBranch','ConditionalBranch'):
            targets = [exit_node if relocated else i['branch_target']]
            return targets + ([end] if flow=='ConditionalBranch' else [])
        need(flow in ('Next','Call','IndirectCall'),'unsupported effect-trace control flow')
        return [end]

    def action(i, relocated):
        return frame_sensitive(i) or i['flow'] in ('Return','Exception','IndirectBranch') or (relocated and i['flow'] in ('UnconditionalBranch','ConditionalBranch'))

    def closure(starts):
        work=list(starts);seen=set();frontier=set()
        while work:
            pc=work.pop()
            if pc in seen:
                continue
            seen.add(pc)
            if pc==exit_node:
                frontier.add(pc);continue
            need(pc in source_at,'original CFG escapes FDE')
            i=source_at[pc]
            if action(i.j,i.reloc is not None):
                frontier.add(pc)
            else:
                work.extend(successors(i.j,i.reloc is not None))
        return frozenset(frontier)

    pending=[(entry,closure([source[0].offset]))];seen=set()
    while pending:
        pc,frontier=pending.pop()
        if (pc,frontier) in seen:
            continue
        seen.add((pc,frontier));need(len(seen)<=100000,'effect-trace validation state budget exceeded')
        need(pc in final_at,'final effect CFG escapes FDE')
        i=final_at[pc];origin=provenance.get(pc);relocated=origin is not None and origin[1].reloc is not None
        if action(i,relocated):
            need(origin is not None,'frame action lacks provenance')
            original=source_at[origin[1].home]
            need(original.offset in frontier,'final path omits/reorders a compiler frame effect at %#x'%pc)
            frontier=closure(successors(original.j,original.reloc is not None))
        for target in successors(i,relocated):
            if target==exit_node:
                need(exit_node in frontier,'final exit omits a compiler frame effect')
            else:
                pending.append((target,frontier))


def final_rows(obj, source_frame, lay, start, size):
    """Verify frame effects and propagate full compiler states on the FINAL CFG.

    Ordinary inserted/sunk instructions inherit the actual incoming state, not
    their old PC's state. Every frame-affecting instruction must retain its
    original operation and pre-state. A state-changing original instruction
    cannot disappear unnoticed because the next protected effect/return and
    joins are checked against the original rules.
    """
    frame=source_frame;source={i.offset:i for i in obj.decode(frame.section)}
    provenance={off:(length,i) for off,length,i in lay.instructions}
    code=[i for i in x86.decode(lay.data) if start<=i['offset']<start+size]
    at={i['offset']:i for i in code};need(code and code[0]['offset']==start,'FDE starts inside an instruction')
    need(code[-1]['offset']+code[-1]['len']==start+size,'FDE ends inside an instruction')
    original_code=[i for i in source.values() if frame.start<=i.offset<frame.start+frame.size]
    verify_effect_traces(original_code,code,provenance,start)
    transitions={};sources={}
    for ins in code:
        pc=ins['offset'];entry=provenance.get(pc)
        if entry is None:
            need(ins['mnemonic']=='nop','unattributed final instruction at %#x'%pc)
            transitions[pc]=None;continue
        length,rewritten=entry;need(length==ins['len'],'encoded instruction length mismatch')
        need(rewritten.home in source,'missing original instruction provenance')
        original=source[rewritten.home];need(frame.start<=original.offset<frame.start+frame.size,'instruction came from a different source FDE')
        sources[pc]=original
        before,after=frame.at(original.offset),frame.at(original.end)
        sensitive=frame_sensitive(ins) or frame_sensitive(original.j)
        if sensitive:
            # Text is relocation-free for frame effects; exact operands/width matter.
            need(ins['gas']==original.gas,'rewritten frame effect at %#x: %s -> %s'%(pc,original.gas,ins['gas']))
        # Linear CFI can switch state at the label AFTER a ret/jump, describing
        # another predecessor, rather than an effect of that exit instruction.
        # Only an actual preserved frame effect transfers a compiler row.
        transitions[pc]=(before,after,sensitive)
    states={start:frame.initial};pending=[start];out={}
    while pending:
        pc=pending.pop();state=states[pc];ins=at[pc];transition=transitions[pc]
        if transition is not None:
            before,after,sensitive=transition
            if sensitive:
                need(state==before,'incoming unwind state disagrees at %s+%#x (%s): %r != %r'%(frame.section,pc,ins['gas'],state,before))
                state=after
        out[pc-start]=states[pc]
        end=pc+ins['len'];flow=ins['flow'];successors=[]
        if flow in ('Return','Exception'):
            # A return must be at the compiler's corresponding restored state.
            if flow=='Return':
                original=sources[pc]
                need(states[pc]==frame.at(original.offset),'return unwind state differs at %s+%#x: %r != %r'%(frame.section,pc,states[pc],frame.at(original.offset)))
        elif flow in ('UnconditionalBranch','ConditionalBranch'):
            target=ins.get('branch_target');rewritten=provenance[pc][1]
            if rewritten.reloc is None:
                need(target in at,'direct branch escapes FDE without external relocation')
                successors.append(target)
            else:
                need(state.cfa==(7,8),'tail call has an unrestored stack')
            if flow=='ConditionalBranch':
                successors.append(end)
        elif flow=='IndirectBranch':
            need(state.cfa==(7,8),'indirect tail has an unrestored stack')
        else:
            need(flow in ('Next','Call','IndirectCall'),'unsupported final control flow '+flow)
            successors.append(end)
        for target in successors:
            need(target in at,'fallthrough outside final FDE')
            if target in states:
                need(states[target]==state,'conflicting unwind states at final CFG join %#x'%target)
            else:
                states[target]=state;pending.append(target)
    # Padding can follow an exit, but every real emitted instruction must be reached.
    for ins in code:
        pc=ins['offset']
        if pc not in states:
            need(ins['mnemonic']=='nop','unreachable non-padding final instruction %#x'%pc)
            previous=max((x for x in out if x<pc-start),default=None)
            out[pc-start]=out[previous] if previous is not None else frame.initial
    compact={};previous=None
    for pc,state in sorted(out.items()):
        if state!=previous:
            compact[pc]=state;previous=state
    return compact


def regenerate(obj, layouts, clones=()):
    frames=read_frames(obj.path);output=[];by_section={}
    for f in frames:
        by_section.setdefault(f.section,[]).append(f)
    need({s for s in obj.code_sections() if obj.sections[s].data}<=set(by_section),'executable compiler section without FDE')
    clone_sources={section:source for section,source in clones}
    for section,lay in layouts.items():
        if not lay.data:
            continue
        source=clone_sources.get(section,section)
        need(source in by_section,'final section lacks source FDE '+section)
        candidates=by_section[source]
        if section in clone_sources:
            need(len(candidates)==1,'clone contains multiple original FDEs')
            f=candidates[0];start=0;size=len(lay.data)
            output.append((section,start,size,final_rows(obj,f,lay,start,size)))
        else:
            for f in candidates:
                need(f.start in lay.offsets and f.start+f.size in lay.offsets,'source FDE boundary not mapped')
                start=lay.offsets[f.start];size=lay.offsets[f.start+f.size]-start
                output.append((section,start,size,final_rows(obj,f,lay,start,size)))
    return encode_frames(output)
