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


def read(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError('%s: not a little-endian ELF64 file' % path)
    e_type, e_machine = struct.unpack_from('<HH', data, 16)
    if e_type != 1 or e_machine != 62:
        raise ValueError('%s: not an x86-64 relocatable object' % path)
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
                                       sh_offset, sh_size, sh_link, sh_info, sh_align, sh_entsize, body))
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
                out.append(ElfReloc(r_offset, RELOC_TYPES.get(r_info & 0xffffffff, 'R_X86_64_%d' % (r_info & 0xffffffff)), r_info >> 32, r_addend))
    return elf
