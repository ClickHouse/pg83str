#!/usr/bin/env python3
"""Require concrete rejection of broken copies of the selected SVE memcpy.

The original is checked by unbounded induction. Mutants use a bounded search:
one concrete input is sufficient to refute a universal correctness claim.
Unsupported semantics, timeout, and solver unknown are not accepted rejections.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from aarch64_machine import Image
from aarch64_objects import run_proof

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=180)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    image = Image(a.elf, sve_vl=16)
    name = 'kl_memcpy_sve_bulk128'
    start, size = image.symbols[name], image.sizes[name]
    instructions = [image.decode(at) for at in range(start, start + size, 4)]
    load = next(j for j in instructions if j['a64'] == 'ld1b')
    store = next(j for j in instructions if j['a64'] == 'st1b')
    predicate = next(j for j in instructions if j['a64'] == 'whilelo')
    raw = a.elf.read_bytes()

    def word(j):
        sec = image.section_at(j['offset'])
        off = sec.offset + j['offset'] - sec.addr
        return off, int.from_bytes(raw[off:off + 4], 'little')

    loff, lw = word(load)
    soff, sw = word(store)
    poff, pw = word(predicate)
    mutations = [('original', None, None),
                 ('omit-first-store', soff, 0xd503201f),
                 ('read-destination', loff, lw & ~(31 << 5)),
                 ('write-source', soff, (sw & ~(31 << 5)) | (1 << 5)),
                 ('empty-first-predicate', poff, (pw & ~(31 << 5)) | (2 << 5))]
    results = []
    for label, offset, replacement in mutations:
        target = a.elf
        if offset is not None:
            data = bytearray(raw)
            data[offset:offset + 4] = replacement.to_bytes(4, 'little')
            target = a.out / (label + '.elf')
            target.write_bytes(data)
        command = [sys.executable, str(HERE / 'prove.py'), '--arch', 'aarch64', '--elf', str(target),
                   '--sve-vl', '16', '--func', name, '--spec', 'memcpy', '--classes', '1', '--verbose']
        command += ['--induct'] if offset is None else ['--max', '4']
        log = a.out / (label + '.log')
        status, code, rows = run_proof(command, log, a.timeout)
        text = log.read_text()
        if offset is None:
            good = status == 'proved_in_model'
        else:
            good = (code not in (None, 0) and len(rows) == 1 and rows[0]['result'] == 'fail'
                    and 'Unsupported' not in text and 'unknown every way' not in text
                    and '((' in rows[0].get('detail', ''))
        results.append({'name': label, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'patch_offset': offset, 'replacement': replacement, 'passed_gate': good,
                        'status': status, 'records': rows})
        (a.out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(label + ': ' + ('passed' if good else 'GATE FAILED'), flush=True)
        if not good:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
