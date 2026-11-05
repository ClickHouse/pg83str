#!/usr/bin/env python3
"""dev/proof/run.py's worker on a host: a JSON list of jobs on stdin, each
an argument list for prove.py; runs them --jobs at a time and prints their
RESULT lines (and a FAIL's text) as they come. --stream: a slot that is
free prints NEXT and reads its job, a JSON line (an empty one: no more),
so that the runner hands out the jobs as the hosts' slots free up."""
import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--stream', action='store_true')
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
                    job = json.loads(line)
                elif not todo:
                    return
                else:
                    job = todo.pop(0)
            # paths in a job are relative to the bundle's root (ix's python on the hosts starts in $HOME)
            job = [str(HERE.parent.parent / x) if prev == '--elf' and not x.startswith('/') else x for prev, x in zip([''] + job, job)]
            p = subprocess.run([sys.executable, str(HERE / 'prove.py'), *job], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = [x for x in p.stdout.splitlines() if x.startswith(('RESULT', 'FAIL'))]
            if not any(x.startswith('RESULT') for x in lines):
                lines.append('RESULT ' + json.dumps({'job': job, 'result': 'error', 'detail': p.stdout[-2000:]}))
            with lock:
                print('\n'.join(lines), flush=True)

    threads = [threading.Thread(target=run) for _ in range(args.jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == '__main__':
    main()
