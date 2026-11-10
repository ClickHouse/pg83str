#!/usr/bin/env python3
"""Final-layout CFI tests on real compiler objects; no target code is executed.

Run after compiling all six original objects with asynchronous unwind tables.
The independent LLVM decoder checks the emitted rows, not our own CFI reader.
"""
from pathlib import Path
import argparse
import bisect
import copy
import hashlib
import json
import re
import struct
import subprocess
import sys

DEV = Path(__file__).resolve().parents[1] / 'dev'
sys.path.insert(0, str(DEV))
import cfi
import cfg
import layout
import opt
import post
from objfile import Object, Ins
import x86

PASSES = 'fold,rename,tail,hot,thread,ktest,kor,kfirst,lea,copy,tramp,vzu'
REGISTERS = {'RAX':0,'RDX':1,'RCX':2,'RBX':3,'RSI':4,'RDI':5,'RBP':6,'RSP':7,
             **{'R%d'%n:n for n in range(8,16)},'RIP':16}
SAVED = (3,6,12,13,14,15,16)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def llvm_rows(text):
    frames = []
    current = None
    for line in text.splitlines():
        match = re.search(r' FDE cie=[0-9a-f]+ pc=([0-9a-f]+)\.\.\.([0-9a-f]+)',line)
        if match:
            current = {'start':int(match[1],16),'size':int(match[2],16)-int(match[1],16),'rows':{}}
            frames.append(current)
        match = re.match(r'  0x([0-9a-f]+): CFA=([A-Z0-9]+)([+-]\d+):\s*(.*)',line)
        if match and current is not None:
            rules=[]
            for rule in filter(None,match[4].split(', ')):
                reg,value=rule.split('=')
                if value == 'same':
                    continue
                offset=re.fullmatch(r'\[CFA([+-]\d+)\]',value)
                assert offset, ('Unsupported LLVM row rule',rule)
                rules.append((REGISTERS[reg],'offset',int(offset[1])))
            current['rows'][int(match[1],16)-current['start']]=cfi.State((REGISTERS[match[2]],int(match[3])),tuple(sorted(rules)))
    return frames


def recover(state, registers, memory):
    """Independent concrete evaluation of CFA and SysV callee/return rules."""
    cfa_value=registers[state.cfa[0]]+state.cfa[1]
    rules={reg:(kind,value) for reg,kind,value in state.rules}
    recovered={}
    for reg in SAVED:
        kind,value=rules.get(reg,('same',0))
        if kind=='same':
            recovered[reg]=registers[reg]
        else:
            assert kind=='offset'
            recovered[reg]=memory[cfa_value+value]
    return cfa_value,recovered


def context(state, cfa_value):
    caller={r:0x1122334400000000+r*0x01010101 for r in SAVED}
    registers={r:caller.get(r,0x8877665500000000+r) for r in range(17)}
    memory={}
    for reg,kind,offset in state.rules:
        assert kind=='offset'
        memory[cfa_value+offset]=caller[reg]
        registers[reg]^=0x55555555
    registers[state.cfa[0]]=cfa_value-state.cfa[1]
    return registers,memory


def rejected(function, name):
    try:
        function()
    except ValueError as error:
        print('PASS refuses',name,':',error)
        return
    raise AssertionError('Negative control accepted: '+name)


def effect_trace_controls():
    # RBP-based CFA can stay unchanged across the RBX/R12 push/pop pairs.
    source=[Ins(j) for j in x86.decode(bytes.fromhex('554889e5534154415c5b5dc3'))]
    def final(sequence):
        instructions=[];provenance={};pc=0
        for original in sequence:
            j=copy.deepcopy(original.j);j['offset']=pc
            ins=copy.deepcopy(original);ins.j['home']=original.offset
            instructions.append(j);provenance[pc]=(j['len'],ins);pc+=j['len']
        return instructions,provenance
    code,origins=final(source)
    cfi.verify_effect_traces(source,code,origins,0)
    for name,sequence in [
        ('omitted push and matching pop',[i for n,i in enumerate(source) if n not in (2,5)]),
        ('reordered pushes',source[:2]+[source[3],source[2]]+source[4:]),
        ('omitted callee-save pop',[i for n,i in enumerate(source)if n!=4]),
    ]:
        code,origins=final(sequence)
        rejected(lambda:cfi.verify_effect_traces(source,code,origins,0),name)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--objects',type=Path,nargs=6,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--dwarfdump',type=Path,required=True)
    a=ap.parse_args()
    assert not a.out.exists()
    a.out.mkdir(parents=True)
    objects=[p.resolve() for p in a.objects]
    sources={str(p):sha(p)for p in objects}
    for stage,extra in [('without-unwind',[]),('with-unwind',['--unwind'])]:
        log=a.out/('test_'+stage+'.log')
        cmd=[sys.executable,str(DEV/'post.py'),'--check','--passes',PASSES,'--out',str(a.out/stage),*extra,*map(str,objects)]
        with log.open('w')as stream:
            subprocess.run(cmd,check=True,stdout=stream,stderr=subprocess.STDOUT)
    decoded=[(p,Object(p))for p in objects];decoded=[(p,obj,obj.decode_all())for p,obj in decoded]
    trampolines={}
    for p,obj,code in decoded:
        trampolines.update(opt.trampolines_of(obj,code))
    args=argparse.Namespace(roundtrip=False)
    import os
    os.environ['POST_PASSES']=PASSES
    for p,obj,code in decoded:
        post.optimize_all(p,obj,code,args,trampolines)
    dispatch=next(obj for _,obj,_ in decoded if any(s.name=='str_configs'for s in obj.symbols))
    clones,_=cfg.specialize(decoded,cfg.read_configs(dispatch),cfg.initial_table(dispatch))
    total_frames=total_pcs=concrete_checks=0;records=[];mutation_done=False
    phase_map=json.loads((DEV/'phases.json').read_text())
    for (path,obj,code),unit_clones in zip(decoded,clones):
        base=path.name
        variants=[(base,False,unit_clones)]
        if post.unit_of(path)!='dispatch':
            variants.append(('jcc-'+base,True,[]))
        original_frames=cfi.read_frames(path)
        for name,jcc,variant_clones in variants:
            before=a.out/'without-unwind'/name;after=a.out/'with-unwind'/name
            assert post.compare(post.describe(before),post.describe(after))is None, name
            frames=cfi.read_frames(after)
            log=a.out/('test_llvm_'+name+'.log')
            with log.open('w')as stream:
                subprocess.run([str(a.dwarfdump),'--eh-frame',str(after)],check=True,stdout=stream,stderr=subprocess.STDOUT)
            independent=llvm_rows(log.read_text());assert len(independent)==len(frames)
            for frame,llvm in zip(frames,independent):
                assert frame.start==llvm['start'] and frame.size==llvm['size'] and frame.rows==llvm['rows'],(name,frame.section)
            lays={section:layout.layout(obj,section,ins,'6',jcc_align=jcc,phase=max([phase_map.get(f.name,0)for f in obj.functions(section)]or[0]) if not jcc else 0)for section,ins in code.items()}
            clone_source={}
            for section,ins,symbol,jcc_cfg,source in variant_clones:
                lays[section]=layout.layout(obj,section,ins,'6',jcc_align=jcc_cfg,source=source,phase=phase_map.get(symbol,0))
                clone_source[section]=source
            for frame,llvm in zip(frames,independent):
                source=clone_source.get(frame.section,frame.section)
                candidates=[f for f in original_frames if f.section==source]
                assert len(candidates)==1
                reference=candidates[0];lay=lays[frame.section]
                expected=cfi.final_rows(obj,reference,lay,frame.start,frame.size)
                keys=sorted(llvm['rows']);expect_keys=sorted(expected)
                # Every byte of every final instruction, not just the row starts.
                for pc in range(frame.size):
                    actual=llvm['rows'][keys[bisect.bisect_right(keys,pc)-1]]
                    wanted=expected[expect_keys[bisect.bisect_right(expect_keys,pc)-1]]
                    for addr in [0x700000010008,0x700000010018]:
                        regs,mem=context(wanted,addr)
                        assert recover(actual,regs,mem)==recover(wanted,regs,mem)
                        concrete_checks+=1
                total_pcs+=frame.size;total_frames+=1
                if not mutation_done:
                    push=next(((off,length,ins)for off,length,ins in lay.instructions if ins.gas=='pushq %rbp'),None)
                    if push:
                        off,length,_=push;bad=copy.deepcopy(lay);data=bytearray(bad.data);assert data[off]==0x55;data[off]=0x53;bad.data=bytes(data)
                        rejected(lambda:cfi.final_rows(obj,reference,bad,frame.start,frame.size),'modified final push register')
                        mutation_done=True
            records.append({'object':name,'code_and_nonCFI_identity':True,'frames':len(frames),'llvm_log_sha256':sha(log),'output_sha256':sha(after)})
    assert mutation_done
    rejected(lambda:cfi.program(b'\x0f',cfi.State()),'opaque DW_CFA_def_cfa_expression')
    rejected(lambda:cfi.program(b'\x0a',cfi.State()),'unbalanced remember_state')
    rejected(lambda:cfi.program(b'\x0b',cfi.State()),'restore without remember')
    rejected(lambda:cfi.program(b'\x04\x00',cfi.State()),'truncated advance')
    effect_trace_controls()
    assert all(sha(p)==sources[str(p)]for p in objects)
    receipt={'status':'PASS_OFFLINE_FINAL_LAYOUT_CFI','input_objects':sources,'output_objects':records,'FDEs':total_frames,'covered_PC_bytes':total_pcs,'concrete_recovery_comparisons':concrete_checks,'code_nonCFI_relocations_symbols_identical':True,'all_rows_independently_decoded_by_LLVM':True,'negative_controls':8,'limits':['Offline compiler-row/CFG/recovery differential; no native signal-unwind or full ClickHouse profile run.','Only explicit unwind mode with frame-removal disabled; unsupported CFI/stack effects fail closed.','Compiler original unwind rules and source frame effects remain trusted; no independent proof of compiler CFI.']}
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    main()
