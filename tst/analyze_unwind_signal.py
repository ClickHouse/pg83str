"""Offline signal-unwind acceptance; samples do not establish all-PC coverage."""
from pathlib import Path
import argparse
import bisect
import hashlib
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dev'))
import elf


def analyze(binary, log, provider_objects, require_recovery):
    image=elf.read(binary,executable=True)
    functions=sorted((s for s in image.symbols if s.type=='STT_FUNC' and s.size and 0<s.shndx<0xff00),key=lambda s:s.value)
    provider_names=set()
    for obj in provider_objects:
        provider_names.update(s.name for s in elf.read(obj).symbols if s.type=='STT_FUNC' and s.shndx!=0)
    provider=[s for s in functions if s.name in provider_names]
    callers=[s for s in functions if s.name=='run_case']
    assert len(callers)==1 and provider
    caller=callers[0];cases={};traces=[];base=None;done=None
    for line in log.read_text().splitlines():
        fields=line.split('\t')
        if fields[0]=='META':
            assert base is None;base=int(fields[1],16);host=int(fields[2]);config_count=int(fields[3])
        elif fields[0]=='CASE':
            config,name,case,count=int(fields[1]),fields[2],int(fields[3]),int(fields[4])
            assert (config,case)not in cases and count>=128
            cases[config,case]={'name':name,'deliveries':count,'pending_timer_ticks_not_delivered':int(fields[5]),'provider_PC_samples':0,'recovered_interrupted_function':0,'recovered_run_case_caller':0,'invalid_trace_counts':0,'sampled_functions':{}}
        elif fields[0]=='TRACE':
            config,case=int(fields[1]),int(fields[2]);ip=int(fields[3],16);n=int(fields[4]);pcs=[int(v,16)for v in fields[5:]]
            assert n==len(pcs) and 0<=n<=48
            traces.append((config,case,ip,pcs))
        elif fields[0]=='DONE':
            assert done is None;done=[int(v)for v in fields[1:]]
        else:
            raise AssertionError('Unexpected functional-test log line '+line)
    assert base is not None and done and len(cases)==done[0]*7 and len(traces)==done[1]
    observed={}
    for config,case,ip,pcs in traces:
        assert (config,case)in cases;record=cases[config,case];observed[config,case]=observed.get((config,case),0)+1
        offset=ip-base;enclosing=[s for s in provider if s.value<=offset<s.value+s.size]
        if not enclosing:
            continue
        record['provider_PC_samples']+=1
        name='/'.join(s.name for s in enclosing);record['sampled_functions'][name]=record['sampled_functions'].get(name,0)+1
        # LLVM's signal-frame unwinder can report the interrupted PC + 1.
        if any(pc in (ip,ip+1)for pc in pcs):
            record['recovered_interrupted_function']+=1
        if any(caller.value<=pc-base<caller.value+caller.size for pc in pcs):
            record['recovered_run_case_caller']+=1
    for key,record in cases.items():
        assert record['deliveries']==observed[key]
        if require_recovery:
            assert record['provider_PC_samples']>0, ('No actual provider PC observed',key,record)
            assert record['recovered_interrupted_function']==record['provider_PC_samples'],('Interrupted function absent',key,record)
            assert record['recovered_run_case_caller']==record['provider_PC_samples'],('Expected caller absent',key,record)
    return {'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'host_bits':host,'registered_configs':config_count,'supported_configs':done[0],'deliveries':len(traces),'cases':[dict(config=k[0],case=k[1],**v)for k,v in sorted(cases.items())],'required_recovery':require_recovery}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--before',type=Path,required=True)
    ap.add_argument('--after',type=Path,required=True)
    ap.add_argument('--before-log',type=Path,required=True)
    ap.add_argument('--after-log',type=Path,required=True)
    ap.add_argument('--provider-objects',type=Path,nargs='+',required=True)
    a=ap.parse_args()
    assert not a.out.exists()
    before=analyze(a.before,a.before_log,a.provider_objects,False)
    after=analyze(a.after,a.after_log,a.provider_objects,True)
    assert [(r['config'],r['case'],r['name'])for r in before['cases']]==[(r['config'],r['case'],r['name'])for r in after['cases']]
    result={'status':'PASS_NATIVE_ASYNC_SIGNAL_UNWIND','before':before,'after':after,'limits':['Timer samples cover observed instruction PCs, not every PC or every dynamic stack state.','Same actual ClickHouse LLVM libunwind archive; standalone signal context and expected run_case caller, not a full ClickHouse profiler validation.','Before/no-CFI observations remain separate and are not required to fail; no timing or performance inference.']}
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'before_deliveries':before['deliveries'],'after_deliveries':after['deliveries'],'cases':len(after['cases'])}))


if __name__=='__main__':
    main()
