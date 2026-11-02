"""The translation units of the library and the -march each is compiled with.
build.py and dev/post.py share this; an object is matched to its unit by the
stem of its file name (v4.o, libstr_v4.a, ours-v4.o)."""

UNITS = {
    'v1': ['-march=x86-64'],
    'v2': ['-march=x86-64-v2'],
    'v3': ['-march=x86-64-v3'],
    'v4': ['-march=x86-64-v4', '-mprefer-vector-width=256'],
    'dispatch': ['-march=x86-64'],
}


def unit_of(path):
    stem = str(path).rsplit('/', 1)[-1].split('.', 1)[0]   # v4.o, v4.c.o, libstr_v4.a, ours-v4.o
    for sep in ('-', '_'):
        stem = stem.rsplit(sep, 1)[-1]
    if stem not in UNITS:
        raise ValueError('%s: cannot tell which unit this is (expected one of %s)' % (path, ', '.join(UNITS)))
    return stem
