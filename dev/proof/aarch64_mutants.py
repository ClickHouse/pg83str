#!/usr/bin/env python3
"""Require the ARM verifier to reject mutations of a proved memcpy binary."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from aarch64_machine import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    image = Image(a.elf)
    start = image.symbols['memcpy_scalar']
    instructions = [image.decode(at) for at in range(start, start + image.sizes['memcpy_scalar'], 4)]
    store = next(j for j in instructions if j['a64'] == 'strb')
    restore = next(j for j in instructions if j['a64'] == 'ldp' and j['ops'][0]['reg'] == 'x29')
    raw = a.elf.read_bytes()

    def word(j):
        section = image.section_at(j['offset'])
        offset = section.offset + j['offset'] - section.addr
        return offset, int.from_bytes(raw[offset:offset + 4], 'little')

    offset, old = word(store)
    frame_offset, frame = word(restore)
    mutations = [('zero-data', offset, (old & ~31) | 31, None),
                 ('write-source', offset, (old & ~(31 << 5)) | (1 << 5), None),
                 ('omit-store', offset, 0xd503201f, None),
                 ('corrupt-saved-register', frame_offset, (frame & ~31) | 28, 'not preserved')]
    results = []
    for name, change_at, replacement, abi_error in [('original', 0, 0, None), *mutations]:
        target = a.elf
        if name != 'original':
            data = bytearray(raw)
            data[change_at:change_at + 4] = replacement.to_bytes(4, 'little')
            target = a.out / (name + '.elf')
            target.write_bytes(data)
        command = [sys.executable, str(HERE / 'prove.py'), '--arch', 'aarch64', '--elf', str(target),
                   '--func', 'memcpy_scalar', '--spec', 'memcpy', '--classes', '1', '--verbose']
        # Prove the original unboundedly. For a mutant, one concrete input
        # refutes the universal claim, so search lengths up to four bytes.
        command += ['--induct'] if name == 'original' else ['--max', '4']
        log_path = a.out / (name + '.log')
        with log_path.open('w') as log:
            r = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        text = log_path.read_text()
        records = [json.loads(line[7:]) for line in text.splitlines() if line.startswith('RESULT ')]
        if name == 'original':
            good = r.returncode == 0 and records and all(row['result'] == 'proved' for row in records)
        elif abi_error:
            good = r.returncode != 0 and abi_error in text and 'Unsupported' not in text
        else:
            good = (r.returncode != 0 and records and all(row['result'] == 'fail' for row in records)
                    and 'unknown every way' not in text and 'Unsupported' not in text
                    and any('((' in row.get('detail', '') for row in records))
        if not good:
            raise SystemExit('mutation gate failed for %s; see %s' % (name, log_path))
        results.append({'name': name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'exit_code': r.returncode, 'records': records})
        print('%s: %s' % (name, 'proved' if name == 'original' else 'rejected'), flush=True)
    (a.out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
