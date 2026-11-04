#!/usr/bin/env python3
"""The lab's outside inputs, pinned by sha256, fetched into one directory.

    deps.py --out DIR

Nothing of these is kept in the repository; every file is checked against its
hash, every URL list has a fallback that serves the same bytes:
  libc6-dev.deb, libgcc-dev.deb  Debian's static glibc 2.44 (the C runtime of
                                 kbench and every IFUNC variant as a kernel)
                                 and gcc's startup objects and libgcc;
  musl.tar.gz                    musl 1.2.5 (its string files are compiled);
  asmlib.zip                     Agner Fog's asmlib (its ELF64 archive is linked);
  freebsd.tar, llvm-libc.tar     the files of freebsd.lock and llvm-libc.lock,
                                 fetched one by one at the pinned commits and
                                 packed (sorted, no times) for the build.
"""
import argparse
import hashlib
import io
import ssl
import tarfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEBIAN = 'https://deb.debian.org/debian/pool/main/g/'
SNAPSHOT = 'https://snapshot.debian.org/file/'   # by sha1: stays when the pool moves on
FILES = {
    'libc6-dev.deb': ('93fed5274dc89e10a9c77dc008e53052ac80517cbe543a03d8760114dbe7509f',
                      [DEBIAN + 'glibc/libc6-dev_2.44-3_amd64.deb', SNAPSHOT + 'db0f43ca3d0237f0fcfe64d3b736b3d8a282ae68']),
    'libgcc-dev.deb': ('a63203467281ca8f62643f6ea5ceb13afd26c11941741f618886b64121829a81',
                       [DEBIAN + 'gcc-16/libgcc-16-dev_16.2.0-4_amd64.deb', SNAPSHOT + '3e3148fe2667caad23f358220c4e1c1fddc91349']),
    'musl.tar.gz': ('a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4',
                    ['https://musl.libc.org/releases/musl-1.2.5.tar.gz', 'https://git.musl-libc.org/cgit/musl/snapshot/musl-1.2.5.tar.gz']),
    'asmlib.zip': ('481d6245051a2b82c06e393b782ba1dd1fe9724209ed9ba5ad5692728ef8da7a',
                   ['https://www.agner.org/optimize/asmlib.zip', 'https://web.archive.org/web/2025id_/https://www.agner.org/optimize/asmlib.zip']),
}
TREES = {
    'freebsd.tar': ('freebsd.lock', 'https://raw.githubusercontent.com/freebsd/freebsd-src/%s/%s'),
    'llvm-libc.tar': ('llvm-libc.lock', 'https://raw.githubusercontent.com/llvm/llvm-project/%s/%s'),
}


# Every file is checked against its sha256; the certificate adds nothing to that,
# and the build environment's python has no CA bundle.
UNVERIFIED = ssl._create_unverified_context()


def download(urls, sha256):
    errors = []
    for url in urls:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=120, context=UNVERIFIED) as r:
                    data = r.read()
                if hashlib.sha256(data).hexdigest() == sha256:
                    return data
                errors.append('%s: sha256 %s' % (url, hashlib.sha256(data).hexdigest()))
                break
            except OSError as e:  # network errors: retry, then the next URL
                errors.append('%s: %s' % (url, e))
                time.sleep(2 * (attempt + 1))
    raise SystemExit('cannot fetch %s:\n  %s' % (sha256, '\n  '.join(errors)))


def lock(name):
    lines = [x for x in (HERE / name).read_text().splitlines() if x.strip()]
    commit = lines[0].split()[2].rstrip(':')
    return commit, [x.split() for x in lines if not x.startswith('#')]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, (sha, urls) in FILES.items():
        path = args.out / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            path.write_bytes(download(urls, sha))
    for name, (lockfile, pattern) in TREES.items():
        commit, entries = lock(lockfile)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for path, sha in sorted(entries):
                data = download([pattern % (commit, path)], sha)
                info = tarfile.TarInfo(path)
                info.size, info.mtime, info.mode = len(data), 0, 0o644
                tar.addfile(info, io.BytesIO(data))
        (args.out / name).write_bytes(buf.getvalue())


if __name__ == '__main__':
    main()
