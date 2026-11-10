"""Padding NOP forms round-trip without replaying stale operands or hints."""
from pathlib import Path
import copy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
import encode
import layout
import x86


def main():
    forms = ['90', '6690', '2e6690', *layout.NOPS.values()]
    for form in forms:
        decoded = x86.decode(bytes.fromhex(form))
        assert len(decoded) == 1 and decoded[0]['mnemonic'] == 'nop'
        blob, fields = encode.encode(decoded[0])
        assert blob == bytes.fromhex(form), (form, blob.hex())

    original = x86.decode(bytes.fromhex('6666666666662e0f1f840000000000'))[0]
    changed = copy.deepcopy(original)
    changed['ops'][0]['mem']['disp'] = 0x12345678
    blob, fields = encode.encode(changed)
    assert blob[:11] == bytes.fromhex('6666666666662e0f1f8400')
    assert fields['disp'] == (11, 4) and blob[11:] == bytes.fromhex('78563412')
    assert x86.decode(blob)[0]['ops'][0]['mem']['disp'] == 0x12345678

    changed = copy.deepcopy(original)
    changed['ops'][0]['mem']['size'] = 4
    blob, _ = encode.encode(changed)
    assert not blob.startswith(b'\x66')
    assert x86.decode(blob)[0]['ops'][0]['mem']['size'] == 4

    changed = copy.deepcopy(original)
    changed['ops'][0]['mem']['seg'] = 'gs'
    blob, _ = encode.encode(changed)
    assert blob.startswith(bytes.fromhex('6665'))
    assert x86.decode(blob)[0]['ops'][0]['mem']['seg'] == 'gs'

    for base in ('r8', 'r13'):
        changed = copy.deepcopy(original)
        changed['ops'][0]['mem']['base'] = base
        blob, _ = encode.encode(changed)
        assert len(blob) <= 15
        assert x86.decode(blob)[0]['ops'][0]['mem']['base'] == base

    changed = copy.deepcopy(original)
    changed['form']['nop_prefixes'] = [0x66] * 20 + [0x2e]
    blob, _ = encode.encode(changed)
    assert len(blob) <= 15
    assert x86.decode(blob)[0]['ops'][0]['mem']['size'] == 2

    changed = copy.deepcopy(original)
    changed['form']['opcode'] = 0x90  # incompatible with the memory NOP form
    blob, _ = encode.encode(changed)
    assert blob != bytes.fromhex(original['bytes'])
    assert x86.decode(blob)[0]['ops'][0]['mem']['size'] == 2

    print('PASS padding NOP forms, changed displacement/field offset, size/segment/register, oversized and incompatible form hints')


if __name__ == '__main__':
    main()
