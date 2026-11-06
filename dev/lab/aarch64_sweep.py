#!/usr/bin/env python3
"""Build placement variants and repeat strict mixed-workload comparisons.

Run on native Linux AArch64 with a pinned Arm reference checkout and the
archive/workload from the lab build. Every build, validation and measurement
has its own log; commands and binary/source identities remain in the output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def choices(text, allowed):
    values = [int(x) for x in text.split(',')]
    if not values or any(x not in allowed for x in values):
        raise argparse.ArgumentTypeError('expected a comma-separated subset of ' + str(allowed))
    return values


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('aor', 'libstr', 'trace', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--cpu', type=int, required=True)
    p.add_argument('--cc', default=os.environ.get('CC', 'clang'))
    p.add_argument('--target', default='aarch64-linux-gnu')
    p.add_argument('--phases', type=lambda x: choices(x, (0, 16, 32, 48)), default=[0, 16, 32, 48])
    p.add_argument('--stands', type=lambda x: choices(x, (0, 1, 2, 3)), default=[0, 3])
    p.add_argument('--passes', type=int, default=9)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--omit-frames', action='store_true')
    p.add_argument('--table', action='append', required=True, help='kbench NAME=CONFIG:SLOT=FN,...')
    a = p.parse_args()
    if not (1 <= a.passes <= 64 and a.repeats >= 1):
        p.error('passes must be 1..64 and repeats must be positive')
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    inputs = {name: {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
              for name, path in (('trace', a.trace), ('libstr', a.libstr))}
    inputs['environment'] = {name: os.environ.get(name, '')
                             for name in ('CC', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'NM', 'OBJCOPY')}
    inputs['cpu'] = a.cpu
    (a.out / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    commands = []

    def run(cmd, stdout, stderr=None):
        command = list(map(str, cmd))
        commands.append({'argv': command, 'stdout': str(stdout), 'stderr': str(stderr) if stderr else 'stdout'})
        (a.out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        with stdout.open('w') as log:
            if stderr:
                with stderr.open('w') as error:
                    subprocess.run(command, stdout=log, stderr=error, check=True)
            else:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)

    for phase in a.phases:
        work = a.out / ('phase%d' % phase)
        work.mkdir(exist_ok=True)
        binary = work / 'kbench'
        cmd = [sys.executable, HERE / 'aarch64.py', '--aor', a.aor.resolve(),
               '--libstr', a.libstr.resolve(), '--work', work / 'work', '--binary', binary,
               '--cc', a.cc, '--target', a.target, '--entry-phase', phase]
        if a.omit_frames:
            cmd.append('--omit-experiment-frames')
        run(cmd, work / 'build.log')
        for stand in a.stands:
            for repeat in range(1, a.repeats + 1):
                label = 'stand%d_run%d' % (stand, repeat)
                cmd = [binary, '--cpu', a.cpu, '--strict-validation', '--no-matrix', '--mixed',
                       '--prims', '__none__', '--trace', a.trace.resolve(), '--passes', a.passes,
                       '--stand', stand, '--meta', work / (label + '_meta.json')]
                for table in a.table:
                    cmd.extend(('--table', table))
                run(cmd, work / ('perf_' + label + '.log'), work / ('test_' + label + '.log'))
        print('phase%d complete' % phase, flush=True)


if __name__ == '__main__':
    main()
