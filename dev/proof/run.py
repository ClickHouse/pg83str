#!/usr/bin/env python3
"""Proofs spread over the lab: one job per function and alignment class.

    dev/proof/run.py --z3 PATH --func evex512.strlen_evex512 --spec strlen (--max 1024 | --induct) [--hosts local,lab1,lab2,lab3]

Ships the prover, the linked library (.build/proof/libstr.elf, ./build proof)
and z3 (a static binary) to each lab host by ssh (no sftp there: a tar on
stdin), splits the jobs by the hosts' threads, runs dev/proof/worker.py on
each in the open ssh session (a detached process dies with it), and prints
every class's result; the exit status is 1 when any failed.
"""
import argparse
import hashlib
import io
import json
import os
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


def bundle(elf, z3):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', compresslevel=1, dereference=True) as tar:  # .build's outputs are links into its store
        for f in FILES:
            tar.add(ROOT / f, arcname=f)
        tar.add(elf, arcname='libstr.elf')
        tar.add(z3, arcname='bin/z3')
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hosts', default='local,lab1,lab2,lab3')
    ap.add_argument('--elf', default=str(ROOT / '.build/proof/libstr.elf'))
    ap.add_argument('--z3', required=True, help='the z3 binary (static)')
    ap.add_argument('--func', required=True, help='comma list')
    ap.add_argument('--spec', required=True)
    ap.add_argument('--max', type=int, default=1024)
    ap.add_argument('--align', default='all')
    ap.add_argument('--induct', action='store_true', help='the loops by induction: every length')
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent))
    import machine
    import prove
    img = machine.Image(args.elf)
    mode = ['--induct'] if args.induct else ['--max', str(args.max)]
    jobs = []
    for f in args.func.split(','):
        classes = range(prove.align_of(img, img.symbols[f])) if args.align == 'all' else [int(x) for x in args.align.split(',')]
        jobs += [['--elf', 'libstr.elf', '--func', f, '--spec', args.spec, *mode, '--align', str(a)] for a in classes]
    hosts = args.hosts.split(',')
    data, digest = bundle(args.elf, args.z3)
    threads = {}
    for h in hosts:
        if h == 'local':
            threads[h] = os.cpu_count()
            continue
        # a directory per bundle: runs with other ELFs at the same time do not touch it
        ssh(h, 'd=$HOME/proof/%s; if [ ! -f $d/.done ]; then rm -rf $d && mkdir -p $d && cd $d && tar xzf - && touch .done; else cat > /dev/null; fi' % digest[:16],
            input=data, check=True)
        threads[h] = int(ssh(h, 'nproc', capture_output=True, text=True, check=True).stdout)
    total = sum(threads.values())
    share, start = {}, 0
    for i, h in enumerate(hosts):
        n = len(jobs) - start if i == len(hosts) - 1 else round(len(jobs) * threads[h] / total)
        share[h] = jobs[start:start + n]
        start += n
    results, lock, t0 = [], threading.Lock(), time.time()

    def run(h):
        if not share[h]:
            return
        if h == 'local':
            env = dict(os.environ, PATH=str(Path(args.z3).parent) + ':' + os.environ['PATH'])
            cmd = [sys.executable, str(HERE / 'worker.py'), '--jobs', str(threads[h])]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
                                 cwd=str(Path(args.elf).parent))
        else:
            d = '$HOME/proof/%s' % digest[:16]
            cmd = ['ssh', '-o', 'LogLevel=ERROR', 'root@%s.mesh' % h, 'cd %s && PATH=%s/bin:$PATH python3 dev/proof/worker.py --jobs %d' % (d, d, threads[h])]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        mine = share[h]
        if h == 'local':
            mine = [[str(Path(args.elf).resolve()) if x == 'libstr.elf' else x for x in job] for job in mine]
        p.stdin.write(json.dumps(mine))
        p.stdin.close()
        for line in p.stdout:
            line = line.rstrip()
            if line.startswith('RESULT '):
                r = json.loads(line[7:])
                r['host'] = h
                with lock:
                    results.append(r)
                    print('%-5s %4d/%d %-28s class %-3s %-6s %5s paths %6.1f s  %s' % (
                        h, len(results), len(jobs), r.get('func', '?'), r.get('align', '?'), r['result'], r.get('paths', ''), r.get('seconds', 0),
                        r.get('detail', '')[:200]), flush=True)
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
    print('%d of %d jobs proved, %d failed or broke, %d missing; %.0f s wall, %.0f s of proving' % (
        len(results) - len(bad), len(jobs), len(bad), len(jobs) - len(results), time.time() - t0, sum(r.get('seconds', 0) for r in results)))
    sys.exit(1 if bad or len(results) != len(jobs) else 0)


if __name__ == '__main__':
    main()
