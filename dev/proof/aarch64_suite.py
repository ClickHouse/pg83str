#!/usr/bin/env python3
"""Record ARM kernel proof coverage against an exact linked ELF artifact.

Every public primitive of both configurations appears in the manifest. A
requested subset leaves the others explicitly not_run; a partial run never
certifies the library. Instruction-model validation is a separate requirement.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import capstone

HERE = Path(__file__).resolve().parent
PRIMITIVES = ['memcpy', 'memmove', 'memset', 'memcmp', 'strlen', 'strcpy', 'strcat',
              'strcmp', 'strspn', 'strcspn', 'memchr']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--functions', help='comma-separated subset; default is every candidate')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    names = {prim + '_' + suffix: prim for suffix in ('scalar', 'neon') for prim in PRIMITIVES}
    selected = set(a.functions.split(',')) if a.functions else set(names)
    if not selected <= names.keys():
        raise SystemExit('unknown candidates: ' + ','.join(sorted(selected - names.keys())))
    manifest = {'elf_sha256': hashlib.sha256(a.elf.read_bytes()).hexdigest(), 'architecture': 'aarch64',
                'decoder': 'capstone ' + capstone.__version__,
                'solver': subprocess.check_output(['z3', '--version'], text=True).strip(),
                'instruction_model': 'experimental; replay and mutation gates required separately',
                'assumptions': 'upstream specs: untagged pointers in the modeled lower 47-bit user range, 4096-byte pages',
                'functions': {name: {'spec': spec, 'status': 'not_run'} for name, spec in names.items()}}
    status = 0
    for name, spec in names.items():
        if name not in selected:
            continue
        log_path = a.out / (name + '.log')
        command = [sys.executable, str(HERE / 'prove.py'), '--arch', 'aarch64', '--elf', str(a.elf),
                   '--func', name, '--spec', spec, '--induct', '--classes', '1', '--verbose']
        with log_path.open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        records = [json.loads(line[7:]) for line in log_path.read_text().splitlines() if line.startswith('RESULT ')]
        proved = result.returncode == 0 and records and all(record['result'] == 'proved' for record in records)
        manifest['functions'][name].update(status='proved_in_model' if proved else 'unproved',
                                            exit_code=result.returncode, records=records)
        status |= not proved
        print(name + ': ' + manifest['functions'][name]['status'], flush=True)
        manifest['complete_kernel_coverage'] = all(item['status'] == 'proved_in_model' for item in manifest['functions'].values())
        (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    raise SystemExit(int(status))


if __name__ == '__main__':
    main()
