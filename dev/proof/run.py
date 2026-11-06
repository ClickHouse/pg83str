#!/usr/bin/env python3
"""Proofs spread over the lab: one job per function and alignment class.

    dev/proof/run.py --z3 PATH --func evex512.strlen_evex512 --spec strlen (--max 1024 | --induct) [--hosts local,lab1,lab2,lab3]
    dev/proof/run.py --z3 PATH --func strlen:sse2.strlen_sse2,memchr:avx2.memchr_avx2 --induct

Ships the prover, the linked library (.build/proof/libstr.elf, ./build proof)
and z3 (a static binary) to each lab host by ssh (no sftp there: a tar on
stdin), runs dev/proof/worker.py on each in the open ssh session (a
detached process dies with it), hands each host's free slot the next job
(the longest specs first), and prints every class's result; the exit
status is 1 when any failed. A function whose code is another's (a
configuration's copy: the same instructions, branch targets, callees'
names and constants read, the same tzcnt) is proved once for both.

Every function is tried first in one class (every pointer in one proof:
strlen sse2 in 8 s, strcpy sse2 in 46 s, memset sse2 in 284 s), for
--one-timeout seconds; when that does not prove it, in its classes
(prove.align_of: 64 or what its code rounds to, 64 for code that rounds
none). The classes are a case split: any number of them covers every
pointer.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FILES = ['dev/x86.py', 'dev/elf.py', 'dev/proof/smt.py', 'dev/proof/machine.py', 'dev/proof/prove.py', 'dev/proof/worker.py']


def ssh(host, cmd, **kw):
    return subprocess.run(['ssh', '-o', 'LogLevel=ERROR', 'root@%s.mesh' % host, cmd], **kw)


def fingerprint(img, entry):
    """The code reachable from entry (jumps and calls followed) with its
    branch targets as places in the walk (a call's with its callee's name
    past the configuration, what the specs' contracts go by) and its
    rip-relative operands as the bytes they read: two functions with the
    same are the same machine to the prover, their addresses aside."""
    names = {a: n for n, a in img.symbols.items()}
    order, index, todo = [], {}, [entry]
    while todo:
        a = todo.pop(0)
        while a not in index:
            index[a] = len(order)
            j = img.decode(a)
            order.append(j)
            if j['mnemonic'] == 'int3':
                break
            if j.get('branch_target') is not None:
                todo.append(j['branch_target'])
            if j['mnemonic'] in ('ret', 'jmp'):
                break
            a += j['len']
    out = []
    for j in order:
        ops = []
        for o in j['ops']:
            if o['kind'] == 'NearBranch':
                tgt = j['branch_target']
                o = ['to', index[tgt], names.get(tgt, '').split('.', 1)[-1]]
            elif o['kind'] == 'Memory' and o['mem'].get('rip_rel'):
                at = j['offset'] + j['len'] + o['mem']['disp']
                o = ['data', o['mem']['size'], [img.byte(at + i) for i in range(o['mem']['size'])]]
            ops.append(o)
        out.append([j['mnemonic'], j['cc'], j['encoding'], j['op_mask'], j['zeroing'], j['bcast'], j['rep'], j['lock'], j['form'], ops])
    return hashlib.sha256(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()


# the specs by their longest classes' time: the longest first, the hosts' last slots on short ones
WEIGHT = ['strcmp', 'strcpy', 'strcat', 'memchr', 'memcmp', 'strspn_long', 'strcspn_long', 'strspn', 'strcspn', 'memmove', 'strcpy_long',
          'memcpy', 'memset', 'strlen']


def bundle(elf, z3, bitwuzla=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', compresslevel=1, dereference=True) as tar:  # .build's outputs are links into its store
        for f in FILES:
            tar.add(ROOT / f, arcname=f)
        tar.add(elf, arcname='libstr.elf')
        tar.add(z3, arcname='bin/z3')
        if bitwuzla:
            tar.add(bitwuzla, arcname='bin/bitwuzla')
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hosts', default='local,lab1,lab2,lab3')
    ap.add_argument('--elf', default=str(ROOT / '.build/proof/libstr.elf'))
    ap.add_argument('--z3', required=True, help='the z3 binary (static)')
    ap.add_argument('--bitwuzla', default=os.environ.get('PROVE_BITWUZLA') or shutil.which('bitwuzla'),
                    help='the bitwuzla binary (static; ix build bin/bitwuzla): the queries without lambdas')
    ap.add_argument('--func', required=True, help='comma list, each a function or spec:function')
    ap.add_argument('--spec', help='the spec of the functions given without one')
    ap.add_argument('--max', type=int, default=1024)
    ap.add_argument('--align', default='all')
    ap.add_argument('--induct', action='store_true', help='the loops by induction: every length')
    ap.add_argument('--one-timeout', type=int, default=3 * 3600, help='seconds the one-class try may take (memchr sse2: 3863 s)')
    ap.add_argument('--job-timeout', type=int, default=12 * 3600, help='seconds any other job may take')
    ap.add_argument('--no-one', action='store_true', help='straight to the classes (a run whose one-class tries an earlier run made)')
    ap.add_argument('--job-memory', type=float, default=7, help='GB a job is given: the jobs a host runs at once (PROVE_MEMORY its solvers\' limit)')
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent))
    import machine
    import prove
    img = machine.Image(args.elf)
    mode = ['--induct'] if args.induct else ['--max', str(args.max)]
    def job(spec, f, a, classes=None, timeout=None):
        return (['--job-timeout', str(timeout)] if timeout else []) + ['--elf', 'libstr.elf', '--func', f, '--spec', spec, *mode] + \
            (['--classes', str(classes)] if classes else []) + ['--align', str(a)]

    jobs, same, rep, fallback = [], {}, {}, {}
    for item in args.func.split(','):
        spec, f = item.split(':') if ':' in item else (args.spec, item)
        key = (spec, not f.startswith(('sse2.', 'sse42.')), fingerprint(img, img.symbols[f]))  # the prover's tzcnt is bsf for those
        if key in rep:
            same[(spec, f)] = rep[key]
            continue
        rep[key] = f
        n = prove.align_of(img, img.symbols[f])
        if args.align != 'all':
            jobs += [job(spec, f, a) for a in args.align.split(',')]
            continue
        classes = [job(spec, f, a, classes=64 if n == 1 else n) for a in range(64 if n == 1 else n)]
        if args.no_one:
            jobs += classes
        else:
            jobs.append(job(spec, f, 0, classes=1, timeout=None if n == 1 else args.one_timeout))
            fallback[(f, spec)] = classes
    for (spec, f), r in sorted(same.items()):
        print('%s %s: the code of %s, proved with it' % (spec, f, r), flush=True)

    def weight(j):
        spec = j[j.index('--spec') + 1]
        return WEIGHT.index(spec) if spec in WEIGHT else len(WEIGHT)
    jobs.sort(key=weight)
    hosts = args.hosts.split(',')
    data, digest = bundle(args.elf, args.z3, args.bitwuzla)
    threads = {}
    for h in hosts:
        if h == 'local':
            threads[h] = min(os.cpu_count(), int(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (args.job_memory * (1 << 30))))
            continue
        # a directory per bundle: runs with other ELFs at the same time do not touch it
        ssh(h, 'd=$HOME/proof/%s; if [ ! -f $d/.done ]; then rm -rf $d && mkdir -p $d && cd $d && tar xzf - && touch .done; else cat > /dev/null; fi' % digest[:16],
            input=data, check=True)
        cpus, mem = ssh(h, 'nproc; awk \'/MemTotal/ {print $2}\' /proc/meminfo', capture_output=True, text=True, check=True).stdout.split()
        threads[h] = min(int(cpus), int(int(mem) / (1 << 20) / args.job_memory))  # a job's solvers may take up to PROVE_MEMORY each
    results, lock, t0 = [], threading.Lock(), time.time()
    todo, out, fell = list(jobs), [0], [0]  # out: jobs handed out without a result yet; fell: one-class tries the classes replaced

    def run(h):
        if not threads[h]:
            return
        if h == 'local':
            env = dict(os.environ, PATH=str(Path(args.z3).parent) + ':' + os.environ['PATH'])
            if args.bitwuzla:
                env['PROVE_BITWUZLA'] = args.bitwuzla
            cmd = [sys.executable, str(HERE / 'worker.py'), '--jobs', str(threads[h]), '--stream', '--timeout', str(args.job_timeout)]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
                                 cwd=str(Path(args.elf).parent))
        else:
            d = '$HOME/proof/%s' % digest[:16]
            env = ' '.join('%s=%s' % (k, v) for k, v in os.environ.items() if k.startswith('PROVE_') and k != 'PROVE_BITWUZLA')
            cmd = ['ssh', '-o', 'LogLevel=ERROR', 'root@%s.mesh' % h, 'cd %s && %s PATH=%s/bin:$PATH python3 dev/proof/worker.py --jobs %d --stream --timeout %d' % (d, env, d, threads[h], args.job_timeout)]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        for line in p.stdout:
            line = line.rstrip()
            if line == 'NEXT':  # a slot free: the next job; WAIT while others may still queue more (a one-class try's); else none
                with lock:
                    j = todo.pop(0) if todo else None
                    out[0] += j is not None
                    word = json.dumps(j) if j else 'WAIT' if out[0] else ''
                if j is not None and h == 'local':
                    word = json.dumps([str(Path(args.elf).resolve()) if x == 'libstr.elf' else x for x in j])
                p.stdin.write(word + '\n')
                p.stdin.flush()
            elif line.startswith('RESULT '):
                r = json.loads(line[7:])
                r['host'] = h
                with lock:
                    out[0] -= 1
                    more = fallback.pop((r.get('func'), r.get('spec')), None)
                    if r['result'] == 'proved' or more is None:
                        more = None
                    else:  # the one-class try did not do: the classes
                        todo[:0] = more
                        jobs.extend(more)
                        fell[0] += 1
                if more is not None:
                    print('%-5s %s %s: one class did not do (%s, %.0f s: %s), %d classes queued' % (
                        h, r['func'], r['spec'], r['result'], r.get('seconds', 0), r.get('detail', '')[-120:].replace('\n', ' '), len(more)), flush=True)
                    continue
                detail = r.get('detail', '')
                if r['result'] == 'error' and detail.strip():  # a traceback: its last line says what broke
                    detail = detail.strip().splitlines()[-1]
                with lock:
                    results.append(r)
                    print('%-5s %4d/%d %-28s class %-3s %-6s %5s paths %6.1f s  %s' % (
                        h, len(results), len(jobs), r.get('func', '?'), r.get('align', '?'), r['result'], r.get('paths', ''), r.get('seconds', 0),
                        detail[:200]), flush=True)
            elif line.startswith('FAIL'):
                with lock:
                    print(h, line, flush=True)
        p.wait()

    ts = [threading.Thread(target=run, args=(h,)) for h in hosts]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    bad = [r for r in results if r['result'] != 'proved']
    total = len(jobs) - fell[0]
    print('%d of %d jobs proved, %d failed or broke, %d missing; %.0f s wall, %.0f s of proving' % (
        len(results) - len(bad), total, len(bad), total - len(results), time.time() - t0, sum(r.get('seconds', 0) for r in results)))
    sys.exit(1 if bad or len(results) != total else 0)


if __name__ == '__main__':
    main()
