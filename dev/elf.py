"""Minimal ELF64 little-endian relocatable object reader: section headers,
symbol table, RELA relocations. Enough for post-processing compiler output;
no external dependencies."""
import struct
from dataclasses import dataclass, field

SECTION_TYPES = {0: 'SHT_NULL', 1: 'SHT_PROGBITS', 2: 'SHT_SYMTAB', 3: 'SHT_STRTAB', 4: 'SHT_RELA', 7: 'SHT_NOTE', 8: 'SHT_NOBITS',
                 14: 'SHT_INIT_ARRAY', 15: 'SHT_FINI_ARRAY', 0x70000001: 'SHT_X86_64_UNWIND'}
SYMBOL_TYPES = {0: 'STT_NOTYPE', 1: 'STT_OBJECT', 2: 'STT_FUNC', 3: 'STT_SECTION', 4: 'STT_FILE'}
SYMBOL_BINDS = {0: 'STB_LOCAL', 1: 'STB_GLOBAL', 2: 'STB_WEAK'}
RELOC_TYPES = {1: 'R_X86_64_64', 2: 'R_X86_64_PC32', 4: 'R_X86_64_PLT32', 10: 'R_X86_64_32', 11: 'R_X86_64_32S',
               41: 'R_X86_64_GOTPCRELX', 42: 'R_X86_64_REX_GOTPCRELX'}
AARCH64_RELOC_TYPES = {257: 'R_AARCH64_ABS64', 258: 'R_AARCH64_ABS32', 259: 'R_AARCH64_ABS16',
                       260: 'R_AARCH64_PREL64', 261: 'R_AARCH64_PREL32', 262: 'R_AARCH64_PREL16',
                       275: 'R_AARCH64_ADR_PREL_PG_HI21', 277: 'R_AARCH64_ADD_ABS_LO12_NC',
                       278: 'R_AARCH64_LDST8_ABS_LO12_NC', 279: 'R_AARCH64_TSTBR14', 280: 'R_AARCH64_CONDBR19',
                       282: 'R_AARCH64_JUMP26', 283: 'R_AARCH64_CALL26', 284: 'R_AARCH64_LDST16_ABS_LO12_NC',
                       285: 'R_AARCH64_LDST32_ABS_LO12_NC', 286: 'R_AARCH64_LDST64_ABS_LO12_NC',
                       299: 'R_AARCH64_LDST128_ABS_LO12_NC'}


@dataclass
class ElfSection:
    index: int
    name: str
    type: str
    flags: int
    offset: int
    size: int
    link: int
    info: int
    align: int
    entsize: int
    data: bytes
    addr: int = 0


@dataclass
class ElfSymbol:
    name: str
    value: int
    size: int
    type: str
    bind: str
    shndx: int      # section index, or 0 (undefined), 0xfff1 (absolute)


@dataclass
class ElfReloc:
    offset: int
    type: str
    symbol: int     # symbol table index
    addend: int


@dataclass
class ElfFile:
    sections: list = field(default_factory=list)
    symbols: list = field(default_factory=list)
    relocs: dict = field(default_factory=dict)   # target section index -> [ElfReloc]

    def section_name(self, index):
        if index == 0:
            return 'UND'
        if index == 0xfff1:
            return 'ABS'
        if index == 0xfff2:
            return 'COMMON'
        return self.sections[index].name


def cstring(data, offset):
    end = data.index(b'\0', offset)
    return data[offset:end].decode()


def read(path, executable=False, machine=62):
    """A relocatable object, or with executable a linked one (its sections
    then carry their addresses)."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError('%s: not a little-endian ELF64 file' % path)
    e_type, e_machine = struct.unpack_from('<HH', data, 16)
    if e_type not in ((1, 2, 3) if executable else (1,)) or e_machine != machine:
        raise ValueError('%s: not a matching-architecture %s' % (path, 'ELF file' if executable else 'relocatable object'))
    e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x3a)
    raw = []
    for i in range(e_shnum):
        raw.append(struct.unpack_from('<IIQQQQIIQQ', data, e_shoff + i * e_shentsize))
    shstr = data[raw[e_shstrndx][4]:raw[e_shstrndx][4] + raw[e_shstrndx][5]]
    elf = ElfFile()
    for i, (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_align, sh_entsize) in enumerate(raw):
        body = b'' if sh_type == 8 else data[sh_offset:sh_offset + sh_size]
        elf.sections.append(ElfSection(i, cstring(shstr, sh_name), SECTION_TYPES.get(sh_type, 'SHT_%d' % sh_type), sh_flags,
                                       sh_offset, sh_size, sh_link, sh_info, sh_align, sh_entsize, body, sh_addr))
    symtab = next((s for s in elf.sections if s.type == 'SHT_SYMTAB'), None)
    if symtab is not None:
        strtab = elf.sections[symtab.link].data
        for k in range(0, len(symtab.data), 24):
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from('<IBBHQQ', symtab.data, k)
            elf.symbols.append(ElfSymbol(cstring(strtab, st_name), st_value, st_size, SYMBOL_TYPES.get(st_info & 15, 'STT_%d' % (st_info & 15)),
                                         SYMBOL_BINDS.get(st_info >> 4, 'STB_%d' % (st_info >> 4)), st_shndx))
    for s in elf.sections:
        if s.type == 'SHT_RELA':
            out = elf.relocs.setdefault(s.info, [])
            for k in range(0, len(s.data), 24):
                r_offset, r_info, r_addend = struct.unpack_from('<QQq', s.data, k)
                types = AARCH64_RELOC_TYPES if machine == 183 else RELOC_TYPES
                prefix = 'R_AARCH64_' if machine == 183 else 'R_X86_64_'
                out.append(ElfReloc(r_offset, types.get(r_info & 0xffffffff, prefix + str(r_info & 0xffffffff)), r_info >> 32, r_addend))
    return elf
