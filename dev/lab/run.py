#!/usr/bin/env python3
"""Run the kernel lab on hosts in parallel; results in .lab/<tag>/<host>/.

    dev/lab/run.py --tag NAME [--hosts local,lab2,lab3] [-- kbench options]

Takes .build/lab/kbench and .build/lab/workload.bin as ./build lab made them
(one binary for every host), ships both to the lab hosts over ssh (they have
no sftp: files go through cat), starts kbench there under setsid on the
idlest core but CPU 0, polls for its end and fetches out.csv.gz, err.log and
meta.json; run.json records the commit, the binary's sha256, the options and
the times. The clock is held at the nominal frequency for the run (freq.py:
boost off, every core at its base) and given back after; on this machine
that needs root: without it the run goes on with a moving clock and says so
(or fix it first: doas python3 dev/lab/freq.py fix --state F, restore after).
The kbench options after -- go to every host; --trace, --meta, --cpu and
--jobs are added here.
"""
import argparse
import datetime
import hashlib
import json
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOSTS = {
    'local': dict(ssh=None, cpu=2, note='AMD Ryzen AI 9 HX 370 (Zen 5), AVX-512 on 256-bit units'),
    'lab1': dict(ssh='root@lab1.mesh', note='Intel Xeon E5-2696 v4 (Broadwell-EP), AVX2; usually loaded'),
    'lab2': dict(ssh='root@lab2.mesh', note='Intel Xeon Gold 6148 (Skylake-SP), AVX-512'),
    'lab3': dict(ssh='root@lab3.mesh', note='Intel Xeon E5-2690 v4 (Broadwell-EP), AVX2'),
}
IDLEST = r'''python3 -c "
import time
def t():
    r={}
    for l in open('/proc/stat'):
        f=l.split()
        if l.startswith('cpu') and l[3:4].isdigit(): r[int(f[0][3:])]=(int(f[4])+int(f[5]),sum(map(int,f[1:])))
    return r
a=t();time.sleep(1);b=t()
print(max((c for c in a if c and c in b),key=lambda c:(b[c][0]-a[c][0])/max(1,b[c][1]-a[c][1])))
"'''


def ssh(host):
    return ['ssh', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30', host]


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def run_host(name, tag, kargs, binary, trace, info, failed):
    h = HOSTS[name]
    out = ROOT / '.lab' / tag / name
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        if not h['ssh']:
            cpu = h['cpu']
            freq = subprocess.run([sys.executable, str(ROOT / 'dev/lab/freq.py'), 'fix', '--state', str(out / 'freq.json')], capture_output=True, text=True)
            fixed = freq.returncode == 0
            (out / 'freq.log').write_text(freq.stdout + freq.stderr)
            if not fixed:
                print('%s: the clock is not held (freq.py needs root): the run goes on with a moving clock' % name, flush=True)
            cmd = [str(binary), '--cpu', str(cpu), '--jobs', '8', '--meta', str(out / 'meta.json'), '--trace', str(trace), *kargs]
            try:
                with open(out / 'out.csv', 'w') as o, open(out / 'err.log', 'w') as e:
                    sh(cmd, stdout=o, stderr=e)
            finally:
                if fixed:
                    sh([sys.executable, str(ROOT / 'dev/lab/freq.py'), 'restore', '--state', str(out / 'freq.json')])
            sh(['gzip', '-f', str(out / 'out.csv')])
        else:
            host, remote = h['ssh'], '/root/lab/' + tag
            sh(ssh(host) + ['mkdir -p %s && rm -f %s/done %s/out.csv %s/out.csv.gz %s/freq.json' % (remote, remote, remote, remote, remote)])
            for local, rname in ((binary, 'kbench'), (trace, 'workload.bin'), (ROOT / 'dev/lab/freq.py', 'freq.py')):
                with open(local, 'rb') as f:
                    sh(ssh(host) + ['cat > %s/%s' % (remote, rname)], stdin=f)
            cpu = subprocess.run(ssh(host) + [IDLEST], capture_output=True, text=True, check=True).stdout.strip()
            inner = ('python3 freq.py fix --state freq.json > freq.log 2>&1; '
                     'nice -n -5 ./kbench --cpu %s --jobs 8 --meta meta.json --trace workload.bin %s > out.csv 2> err.log; rc=$?; '
                     'python3 freq.py restore --state freq.json >> freq.log 2>&1; echo $rc > done') % (cpu, ' '.join(shlex.quote(a) for a in kargs))
            # setsid: a session of its own, so that the end of this ssh session does not take it along
            sh(ssh(host) + ['cd %s && chmod +x kbench && setsid sh -c %s < /dev/null > /dev/null 2>&1 &' % (remote, shlex.quote(inner))])
            print('%s: running on cpu %s' % (name, cpu), flush=True)
            while subprocess.run(ssh(host) + ['test -f %s/done' % remote]).returncode:
                time.sleep(60)
            rc = subprocess.run(ssh(host) + ['cat %s/done' % remote], capture_output=True, text=True).stdout.strip()
            sh(ssh(host) + ['cd %s && gzip -f out.csv' % remote])
            for rname in ('out.csv.gz', 'err.log', 'meta.json', 'freq.log'):
                with open(out / rname, 'wb') as f:
                    sh(ssh(host) + ['cat %s/%s' % (remote, rname)], stdout=f)
            if rc != '0':
                raise RuntimeError('kbench exited with %s, see %s' % (rc, out / 'err.log'))
        (out / 'run.json').write_text(json.dumps(dict(info, host=name, note=h['note'], cpu=cpu, started=started,
                                                      ended=datetime.datetime.now(datetime.timezone.utc).isoformat()), indent=2) + '\n')
        print('%s: done -> %s' % (name, out), flush=True)
    except Exception as e:  # noqa: BLE001
        failed[name] = repr(e)
        print('%s: FAILED %r' % (name, e), flush=True)


def main():
    argv = sys.argv[1:]
    kargs = []
    if '--' in argv:
        i = argv.index('--')
        argv, kargs = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--hosts', default='local,lab2,lab3')
    ap.add_argument('--binary', type=Path, default=ROOT / '.build/lab/kbench')
    ap.add_argument('--trace', type=Path, default=ROOT / '.build/lab/workload.bin')
    args = ap.parse_args(argv)
    binary, trace = args.binary.resolve(), args.trace.resolve()
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    info = dict(tag=args.tag, commit=commit, dirty=dirty, kbench_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(), options=kargs)
    failed = {}
    threads = [threading.Thread(target=run_host, args=(n, args.tag, kargs, binary, trace, info, failed)) for n in args.hosts.split(',')]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print('failed: %s' % (failed or 'none'))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
