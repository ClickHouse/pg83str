#!/usr/bin/env python3
"""Build the independent Linux AArch64 eleven-entry selected provider."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'aarch64/selected'
NEON = ['aarch64_neon_bulk', 'aarch64_neon_extra', 'aarch64_neon_length',
        'aarch64_neon_membership', 'aarch64_neon_strings', 'dispatch',
        'memory_copy', 'memory_fill', 'memory_search']
SVE = ['aarch64_sve_bulk', 'aarch64_sve_extra', 'sve_dispatch',
       'tiny_copy_sve', 'memory_search_sve']
LEAF = {'memory_copy', 'memory_fill', 'memory_search', 'tiny_copy_sve', 'memory_search_sve'}
COMMON = ['-std=gnu11', '-O3', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
          '-fno-lto', '-fPIC', '-ffunction-sections', '-fdata-sections',
          '-falign-functions=64', '-falign-loops=32', '-fasynchronous-unwind-tables',
          '-fno-omit-frame-pointer', '-mno-omit-leaf-frame-pointer']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    paths = [SOURCE / (name + '.c') for name in NEON + SVE]
    paths += [SOURCE / 'aarch64_helpers.h', SOURCE / 'kernels.h', SOURCE / 'str.h',
              SOURCE / 'LICENSE', SOURCE / 'provenance.json']
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Expected regular selected-provider input: ' + str(path))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def run(argv):
    print(shlex.join(argv), flush=True)
    subprocess.run(argv, check=True, cwd=ROOT)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def build(args, cc, ar):
    initial = inputs()
    version = subprocess.check_output([*cc, '--version'], text=True, cwd=ROOT)
    if 'clang' not in version.lower():
        raise ValueError('The selected provider currently requires Clang')
    ar_version = subprocess.check_output([*ar, '--version'], text=True, cwd=ROOT)
    objects = args.out / 'objects'
    objects.mkdir(exist_ok=True)
    commands = []
    extra = shlex.split(os.environ.get('CPPFLAGS', '')) + shlex.split(os.environ.get('CFLAGS', ''))
    for name in NEON + SVE:
        source = SOURCE / (name + '.c')
        obj = objects / (name + '.o')
        flags = ['-march=armv8-a+sve', '-msve-vector-bits=scalable'] if name in SVE else ['-march=armv8-a']
        if name in LEAF:
            flags += ['-momit-leaf-frame-pointer', '-fasynchronous-unwind-tables']
        argv = [*cc, *extra, '--target=' + args.target, *COMMON, *flags, '-c', str(source), '-o', str(obj)]
        commands.append({'directory': str(ROOT), 'file': str(source), 'output': str(obj), 'arguments': argv})
        run(argv)
    # A fresh archive prevents members removed from the source list surviving a rebuild.
    with tempfile.TemporaryDirectory(prefix='archive-', dir=args.out) as stage:
        archive = Path(stage) / 'libstr-prefixed.a'
        archive_argv = [*ar, 'rcs', str(archive), *[str(objects / (name + '.o')) for name in NEON + SVE]]
        run(archive_argv)
        if inputs() != initial:
            raise ValueError('Selected-provider inputs changed during compilation')
        archive.replace(args.out / 'libstr-prefixed.a')
    write_json(args.out / 'compile_commands.json', commands)
    write_json(args.out / 'build.json', {
        'provider': 'Linux AArch64 selected eleven-entry provider', 'target': args.target,
        'compiler': cc, 'compiler_version': version, 'archiver': ar, 'archiver_version': ar_version,
        'inputs': initial, 'compile_commands': commands, 'archive_command': archive_argv,
        'objects': {name + '.o': digest(objects / (name + '.o')) for name in NEON + SVE},
        'archive_sha256': digest(args.out / 'libstr-prefixed.a'),
        'scope': 'Compiler output; no x86 post-pass. A rebuild is not a transfer of historical object certificates.'})


def test(args, cc):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise ValueError('selected-test executes target binaries and requires native Linux AArch64')
    receipt = json.loads((args.out / 'build.json').read_text())
    archive = args.out / 'libstr-prefixed.a'
    if receipt['target'] != args.target or receipt['inputs'] != inputs() or receipt['archive_sha256'] != digest(archive):
        raise ValueError('Build receipt does not match current selected-provider inputs/archive')
    source = ROOT / 'tst/aarch64_selected.c'
    source_hash = digest(source)
    extra = shlex.split(os.environ.get('CPPFLAGS', '')) + shlex.split(os.environ.get('CFLAGS', ''))
    link = shlex.split(os.environ.get('LDFLAGS', ''))
    modes = []
    for forced in (False, True):
        name = 'test-neon' if forced else 'test'
        executable = args.out / name
        flags = ['-DSTR_TEST_NO_SVE', '-Wl,--wrap=getauxval'] if forced else []
        argv = [*cc, *extra, '--target=' + args.target, '-std=gnu11', '-O2', '-march=armv8-a',
                '-fno-builtin', *flags, str(source), str(archive), *link, '-UNDEBUG', '-o', str(executable)]
        run(argv)
        run([str(executable)])
        modes.append({'name': name, 'compile_command': argv, 'argv': [str(executable)], 'returncode': 0})
        if not forced:
            for vl in args.sve_vl:
                run([str(executable), str(vl)])
                modes.append({'name': 'test-vl' + str(vl), 'argv': [str(executable), str(vl)], 'returncode': 0})
    if inputs() != receipt['inputs'] or digest(archive) != receipt['archive_sha256'] or digest(source) != source_hash:
        raise ValueError('Selected-provider test inputs changed during execution')
    write_json(args.out / 'test.json', {'target': args.target, 'archive_sha256': receipt['archive_sha256'],
        'test_source_sha256': source_hash, 'modes': modes,
        'scope': 'Independent host-libc comparison. Forced feature masking is linked only into the test executable; unsupported requested VL is an error.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'test'])
    parser.add_argument('--target', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cc', default=os.environ.get('CC', 'clang'))
    parser.add_argument('--ar', default=os.environ.get('AR', 'llvm-ar'))
    parser.add_argument('--sve-vl', type=int, action='append', default=[])
    args = parser.parse_args()
    if args.target.split('-', 1)[0] not in ('aarch64', 'arm64') or 'linux' not in args.target:
        raise ValueError('The selected provider requires an AArch64 Linux target')
    if any(v <= 0 or v % 16 for v in args.sve_vl):
        raise ValueError('Requested SVE vector lengths must be positive multiples of 16 bytes')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    cc, ar = shlex.split(args.cc), shlex.split(args.ar)
    if not cc or not ar:
        raise ValueError('CC and AR must name explicit tools')
    with (args.out / '.build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == 'build':
            build(args, cc, ar)
        else:
            test(args, cc)


if __name__ == '__main__':
    main()
