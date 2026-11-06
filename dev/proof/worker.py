#!/usr/bin/env python3
"""dev/proof/run.py's worker on a host: a JSON list of jobs on stdin, each
an argument list for prove.py; runs them --jobs at a time and prints their
RESULT lines (and a FAIL's text) as they come. --stream: a slot that is
free prints NEXT and reads its job, a JSON line (WAIT: ask again soon; an
empty one: no more), so that the runner hands out the jobs as the hosts'
slots free up. A job may start with --job-timeout N: its own limit."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--stream', action='store_true')
    ap.add_argument('--timeout', type=int, default=6 * 3600, help='seconds a job may take (then killed: an error)')
    args = ap.parse_args()
    todo = None if args.stream else json.load(sys.stdin)
    lock = threading.Lock()

    def run():
        while True:
            with lock:
                if args.stream:
                    print('NEXT', flush=True)
                    line = sys.stdin.readline().strip()
                    if not line:
                        return
                    job = None if line == 'WAIT' else json.loads(line)
                elif not todo:
                    return
                else:
                    job = todo.pop(0)
            if job is None:
                time.sleep(20)
                continue
            limit = args.timeout
            if job[0] == '--job-timeout':
                limit, job = int(job[1]), job[2:]
            # paths in a job are relative to the bundle's root (ix's python on the hosts starts in $HOME)
            job = [str(HERE.parent.parent / x) if prev == '--elf' and not x.startswith('/') else x for prev, x in zip([''] + job, job)]
            p = subprocess.Popen([sys.executable, str(HERE / 'prove.py'), *job], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                out = p.communicate(timeout=limit)[0]
            except subprocess.TimeoutExpired:
                p.terminate()  # SIGTERM: prove.py takes its solvers down
                out = p.communicate()[0] + '\nkilled after %d s' % limit
            lines = [x for x in out.splitlines() if x.startswith(('RESULT', 'FAIL'))]
            if not any(x.startswith('RESULT') for x in lines):
                arg = dict(zip(job, job[1:]))
                lines.append('RESULT ' + json.dumps({'job': job, 'func': arg.get('--func'), 'spec': arg.get('--spec'), 'classes': arg.get('--classes'), 'align': arg.get('--align'),
                                                     'result': 'error', 'detail': out[-2000:]}))
            with lock:
                print('\n'.join(lines), flush=True)

    threads = [threading.Thread(target=run) for _ in range(args.jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == '__main__':
    main()
