#!/usr/bin/env python3
"""Hold the CPU clock at its base frequency for a measurement, and give it back.

    freq.py fix --state FILE      save the current settings to FILE, then fix the clock
    freq.py restore --state FILE  put back what FILE holds
    freq.py show                  what the controls say now

Root only. Turbo or boost off and the cores at their nominal frequency, by
whatever the host has:
  cpufreq in sysfs   cpufreq/boost = 0 (AMD) or intel_pstate/no_turbo = 1, every
                     policy's governor = performance: with boost off its highest
                     frequency is the nominal one;
  HWP without cpufreq (IA32_PM_ENABLE set; Skylake-SP with no driver loaded)
                     IA32_HWP_REQUEST of every core: minimum = maximum = desired
                     = the guaranteed ratio of IA32_HWP_CAPABILITIES;
  neither            IA32_MISC_ENABLE bit 38 (turbo off) on every core.
AVX-512 frequency licenses still apply: a core running 512-bit code drops below
the nominal clock on Intel; that is part of what the code costs.
"""
import argparse
import json
import os
import struct
import sys
from pathlib import Path

SYS = Path('/sys/devices/system/cpu')
MISC_ENABLE, PM_ENABLE, HWP_CAP, HWP_REQUEST = 0x1a0, 0x770, 0x771, 0x774


def cpus():
    return sorted(int(p.name[3:]) for p in SYS.glob('cpu[0-9]*'))


def rd(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def wr(path, value):
    Path(path).write_text(str(value))


def msr_read(cpu, reg):
    fd = os.open('/dev/cpu/%d/msr' % cpu, os.O_RDONLY)
    try:
        return struct.unpack('<Q', os.pread(fd, 8, reg))[0]
    finally:
        os.close(fd)


def msr_write(cpu, reg, value):
    fd = os.open('/dev/cpu/%d/msr' % cpu, os.O_WRONLY)
    try:
        os.pwrite(fd, struct.pack('<Q', value), reg)
    finally:
        os.close(fd)


def state():
    s = {'boost': rd(SYS / 'cpufreq/boost'), 'no_turbo': rd(SYS / 'intel_pstate/no_turbo'), 'policies': {}, 'msr': {}}
    for c in cpus():
        gov = rd(SYS / ('cpu%d/cpufreq/scaling_governor' % c))
        if gov is not None:
            s['policies'][c] = gov
    if not s['policies'] and Path('/dev/cpu/0/msr').exists():
        for c in cpus():
            hwp = msr_read(c, PM_ENABLE) & 1
            s['msr'][c] = {'hwp': hwp, 'value': msr_read(c, HWP_REQUEST if hwp else MISC_ENABLE)}
    return s


def fix(s):
    done = []
    if s['boost'] is not None:
        wr(SYS / 'cpufreq/boost', 0)
        done.append('boost off')
    if s['no_turbo'] is not None:
        wr(SYS / 'intel_pstate/no_turbo', 1)
        done.append('no_turbo')
    for c in s['policies']:
        wr(SYS / ('cpu%d/cpufreq/scaling_governor' % c), 'performance')
    if s['policies']:
        done.append('governor performance on %d cpus' % len(s['policies']))
    for c, m in s['msr'].items():
        if m['hwp']:
            g = (msr_read(c, HWP_CAP) >> 8) & 0xff  # guaranteed performance
            msr_write(c, HWP_REQUEST, g | g << 8 | g << 16)  # min, max, desired; EPP 0
        else:
            msr_write(c, MISC_ENABLE, m['value'] | 1 << 38)
    if s['msr']:
        first = next(iter(s['msr'].values()))
        done.append(('HWP request at the guaranteed ratio %d' % ((msr_read(0, HWP_CAP) >> 8) & 0xff)) if first['hwp'] else 'turbo off by MSR')
    return done


def restore(s):
    for c, m in s['msr'].items():
        msr_write(int(c), HWP_REQUEST if m['hwp'] else MISC_ENABLE, m['value'])
    for c, gov in s['policies'].items():
        wr(SYS / ('cpu%s/cpufreq/scaling_governor' % c), gov)
    if s['no_turbo'] is not None:
        wr(SYS / 'intel_pstate/no_turbo', s['no_turbo'])
    if s['boost'] is not None:
        wr(SYS / 'cpufreq/boost', s['boost'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=('fix', 'restore', 'show'))
    ap.add_argument('--state', type=Path)
    args = ap.parse_args()
    if args.action == 'show':
        print(json.dumps(state(), indent=1))
        return
    if not args.state:
        sys.exit('--state FILE')
    if args.action == 'fix':
        s = state()
        args.state.write_text(json.dumps(s))
        print('fixed: ' + ', '.join(fix(s) or ['nothing to fix']))
    else:
        restore(json.loads(args.state.read_text()))
        print('restored from %s' % args.state)


if __name__ == '__main__':
    main()
