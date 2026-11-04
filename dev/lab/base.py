#!/usr/bin/env python3
"""Another revision of ours in the lab's binary, for an A/B of two revisions.

    ix run lib/c bld/compiler -- python3 dev/lab/base.py REV [-j N]

Two kbench binaries differ in the layout of everything, the stand's loop and
glibc's functions included, and that alone moves a trace by 3-10% (Broadwell:
glibc's mixed trace 13.2 ns in one build, 13.7 in the next; Skylake-SP: ours'
memcmp and strcmp 10% apart with their code unchanged). So the baseline goes
into the same binary: libstr.a built at REV in a worktree (.lab/base/<sha>),
copied to .lab/base/libstr.a with the revision in .lab/base/rev, then
./build lab_base: kbench with it beside this tree's (lab.py --base, its
symbols prefixed base_) as .build/lab/kbench-base. Then

    dev/lab/run.py --tag T --binary .build/lab/kbench-base -- --libs ...
    dev/lab/libs.py 'T*'

gives ours and base side by side (the kernels base/<config> in the matrix and
the per-primitive traces). The revisions' struct str_table and str_config must
agree: the link stops at a static assertion otherwise.
"""
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rev')
    ap.add_argument('-j', default='12')
    args = ap.parse_args()
    sha = subprocess.run(['git', '-C', ROOT, 'rev-parse', '--verify', args.rev + '^{commit}'], capture_output=True, text=True, check=True).stdout.strip()
    tree = ROOT / '.lab/base' / sha
    if not (tree / '.git').exists():
        tree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', ROOT, 'worktree', 'add', '--detach', '--force', tree, sha], check=True)
    subprocess.run(['./build', '-j', args.j, 'libstr'], cwd=tree, check=True)
    out = ROOT / '.lab/base'
    (out / 'libstr.a').write_bytes((tree / '.build/libstr.a').read_bytes())
    (out / 'rev').write_text(sha[:12] + '\n')
    subprocess.run(['./build', '-j', args.j, 'lab_base'], cwd=ROOT, check=True)
    print('%s: base %s' % (ROOT / '.build/lab/kbench-base', sha[:12]))

if __name__ == '__main__':
    main()
