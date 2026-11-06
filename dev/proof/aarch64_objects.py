#!/usr/bin/env python3
"""Link exact AArch64 objects and prove a selected public table's machine code.

The compiler is never invoked. --elf accepts an ELF linked on another host;
otherwise --linker links the supplied objects, retaining only selected roots
and their dependencies. Code outside the bits covered by known relocations
must match the input objects, and optionally the measured benchmark.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import elf
import capstone

MASKS = {'R_AARCH64_CALL26': (1 << 26) - 1, 'R_AARCH64_JUMP26': (1 << 26) - 1,
         'R_AARCH64_ADR_PREL_PG_HI21': (3 << 29) | (((1 << 19) - 1) << 5),
         'R_AARCH64_ADD_ABS_LO12_NC': 4095 << 10,
         **{'R_AARCH64_LDST%d_ABS_LO12_NC' % n: 4095 << 10 for n in (8, 16, 32, 64, 128)}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selection(path):
    data = json.loads(path.read_text())
    if 'selected_table' in data:
        return dict(item.split('=', 1) for item in data['selected_table'].split(':', 1)[1].split(','))
    return data


def provenance(objects, linked, benchmark=None):
    image = elf.read(linked, executable=True, machine=183)
    images = [('proof', image)]
    if benchmark is not None:
        images.append(('benchmark', elf.read(benchmark, executable=True, machine=183)))
    tables = {label: {s.name: s for s in im.symbols if s.type == 'STT_FUNC' and s.shndx != 0} for label, im in images}
    records = []
    seen = set()
    for path in objects:
        obj = elf.read(path, machine=183)
        item = {'path': str(path), 'sha256': digest(path), 'functions': {}}
        for sym in obj.symbols:
            if sym.type != 'STT_FUNC' or not sym.size or sym.shndx == 0 or sym.name not in tables['proof']:
                continue
            if sym.name in seen:
                raise ValueError('ambiguous input function: ' + sym.name)
            seen.add(sym.name)
            sec = obj.sections[sym.shndx]
            original = sec.data[sym.value:sym.value + sym.size]
            masks = [0] * (sym.size // 4)
            relocations = []
            for r in obj.relocs.get(sec.index, []):
                if sym.value <= r.offset < sym.value + sym.size:
                    if r.type not in MASKS or r.offset & 3:
                        raise ValueError('unsupported code relocation: ' + r.type)
                    masks[(r.offset - sym.value) // 4] |= MASKS[r.type]
                    relocations.append({'offset': r.offset - sym.value, 'type': r.type,
                                        'symbol': obj.symbols[r.symbol].name, 'addend': r.addend})
            row = {'input_bytes_sha256': hashlib.sha256(original).hexdigest(), 'size': sym.size,
                   'relocations': relocations, 'linked': {}}
            for label, im in images:
                target = tables[label].get(sym.name)
                if target is None or target.size != sym.size:
                    raise ValueError('missing or differently sized %s function: %s' % (label, sym.name))
                section = im.sections[target.shndx]
                off = target.value - section.addr
                raw = section.data[off:off + target.size]
                for i, mask in enumerate(masks):
                    a = int.from_bytes(original[4 * i:4 * i + 4], 'little')
                    b = int.from_bytes(raw[4 * i:4 * i + 4], 'little')
                    if (a ^ b) & ~mask:
                        raise ValueError('%s code differs outside relocation bits: %s+%#x' % (label, sym.name, i * 4))
                row['linked'][label] = {'address': target.value, 'bytes_sha256': hashlib.sha256(raw).hexdigest()}
            item['functions'][sym.name] = row
        records.append(item)
    missing = set(tables['proof']) - seen
    if missing:
        raise ValueError('linked functions without object provenance: ' + ','.join(sorted(missing)))
    return records


def run_proof(command, log_path, timeout):
    with log_path.open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()
            return 'timeout', None, []
    text = log_path.read_text()
    rows = [json.loads(line[7:]) for line in text.splitlines() if line.startswith('RESULT ')]
    if code == 0 and len(rows) == 1 and rows[0]['result'] == 'proved':
        return 'proved_in_model', code, rows
    if any(row['result'] == 'fail' for row in rows):
        status = 'counterexample_or_failed_invariant'
    elif 'Unsupported' in text:
        status = 'unsupported'
    else:
        status = 'unproved'
    return status, code, rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--objects', nargs='+', type=Path, required=True)
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--elf', type=Path, help='already linked object-only proof ELF')
    p.add_argument('--linker', default='ld.lld')
    p.add_argument('--benchmark', type=Path, help='also check input code identity against this measured ELF')
    p.add_argument('--sve-vl', type=int, choices=[16], required=True)
    p.add_argument('--functions', help='comma-separated subset; omitted entries remain explicitly not_run')
    p.add_argument('--timeout', type=float, default=300, help='seconds per function')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--max', type=int, help='bounded proof instead of unbounded induction; recorded distinctly')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    chosen = selection(a.selection)
    requested = set(a.functions.split(',')) if a.functions else set(chosen.values())
    if requested - set(chosen.values()):
        p.error('unknown selected functions')
    target = a.elf or a.out / 'kernels.elf'
    link_command = None
    if a.elf is None:
        link_command = [a.linker, '--no-relax', '--gc-sections', '-e', '0', '-o', str(target)]
        link_command += ['--undefined=' + name for name in chosen.values()] + list(map(str, a.objects))
        with (a.out / 'build_link.log').open('w') as log:
            subprocess.run(link_command, stdout=log, stderr=subprocess.STDOUT, check=True)
    manifest = {'architecture': 'aarch64', 'elf_sha256': digest(target), 'link_command': link_command,
                'benchmark_sha256': digest(a.benchmark) if a.benchmark else None,
                'provenance_scope': 'exact nonrelocation instruction bits; relocation application and data layout trust the linker',
                'objects': provenance(a.objects, target, a.benchmark), 'sve_vl_bytes': a.sve_vl,
                'decoder': 'capstone ' + capstone.__version__,
                'solver': subprocess.check_output(['z3', '--version'], text=True).strip(),
                'model_sha256': {path.name: digest(path) for path in (HERE / 'aarch64_machine.py', HERE / 'aarch64_vectors.py',
                                                                     HERE / 'machine.py', HERE / 'prove.py', HERE / 'smt.py')},
                'assumptions': 'untagged lower-47-bit addresses,4096-byte pages,standard AAPCS64,explicit VL16; model validation separate',
                'proof_mode': 'unbounded_induction' if a.max is None else 'bounded', 'max': a.max,
                'functions': {name: {'spec': spec, 'status': 'not_run'} for spec, name in chosen.items()}}
    manifest_path = a.out / 'manifest.json'

    def save():
        manifest['complete_selected_kernel_coverage'] = all(v['status'] == 'proved_in_model' for v in manifest['functions'].values())
        manifest['full_library_certified'] = False
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')

    save()
    for name, row in manifest['functions'].items():
        if name not in requested:
            continue
        command = [sys.executable, str(HERE / 'prove.py'), '--arch', 'aarch64', '--elf', str(target), '--sve-vl', str(a.sve_vl),
                   '--func', name, '--spec', row['spec'], '--classes', '1', '--workers', str(a.workers), '--verbose']
        command += ['--induct'] if a.max is None else ['--max', str(a.max)]
        status, code, results = run_proof(command, a.out / (name + '.log'), a.timeout)
        row.update(status=status, exit_code=code, results=results, command=command)
        save()
        print(name + ': ' + status, flush=True)
    raise SystemExit(int(any(manifest['functions'][name]['status'] != 'proved_in_model' for name in requested)))


if __name__ == '__main__':
    main()
