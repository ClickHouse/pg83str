"""What ./build proof proves: every configuration's copy of every function,
by its spec, and how (build.py makes one node per entry, dev/proof/node.py
runs it).

A node is <spec>_<config> (strspn_sse2). It proves the function in one
class (every pointer in one proof) unless SPLIT gives it a number of
classes: then each class is a node of its own, <spec>_<config>_<k>, and the
function's node stands for all of them. SAME names the copies whose code is
another's (the post pass gives each configuration its own): their node
checks that, by the prover's fingerprint, and takes that one's proof.
DEPS: the specs a spec's proof takes its callees from (strcat: strlen's,
strcpy's and strcpy_long's contracts, each proved on its own)."""

CONFIGS = ('sse2', 'sse42', 'avx2', 'evex', 'evex512', 'evex512r')

# spec -> the symbol of each configuration's copy
FUNCS = {
    'strlen': {'sse2': 'sse2.strlen_sse2', 'sse42': 'sse42.strlen_sse2', 'avx2': 'avx2.strlen_avx2', 'evex': 'evex.strlen_evex512',
               'evex512': 'evex512.strlen_evex512', 'evex512r': 'evex512r.strlen_evex512'},
    'memchr': {'sse2': 'sse2.memchr_sse2', 'sse42': 'sse42.memchr_sse2', 'avx2': 'avx2.memchr_avx2', 'evex': 'evex.memchr_evex',
               'evex512': 'evex512.memchr_evex512', 'evex512r': 'evex512r.memchr_evex512'},
    'memset': {'sse2': 'sse2.memset_sse2', 'sse42': 'sse42.memset_sse2', 'avx2': 'avx2.memset_avx2', 'evex': 'evex.memset_evex512',
               'evex512': 'evex512.memset_evex512', 'evex512r': 'evex512r.memset_rep'},
    'memcpy': {'sse2': 'sse2.memcpy_sse2', 'sse42': 'sse42.memcpy_sse2', 'avx2': 'avx2.memcpy_avx2', 'evex': 'evex.memcpy_evex512',
               'evex512': 'evex512.memcpy_evex512', 'evex512r': 'evex512r.memcpy_evex512'},
    'memmove': {'sse2': 'sse2.memmove_sse2', 'sse42': 'sse42.memmove_sse2', 'avx2': 'avx2.memmove_avx2', 'evex': 'evex.memmove_evex512',
                'evex512': 'evex512.memmove_evex512', 'evex512r': 'evex512r.memmove_evex512'},
    'memcmp': {'sse2': 'sse2.memcmp_sse2', 'sse42': 'sse42.memcmp_sse2', 'avx2': 'avx2.memcmp_avx2', 'evex': 'evex.memcmp_evex512',
               'evex512': 'evex512.memcmp_evex512', 'evex512r': 'evex512r.memcmp_evex512'},
    'strcmp': {'sse2': 'sse2.strcmp_sse2', 'sse42': 'sse42.strcmp_sse2', 'avx2': 'avx2.strcmp_avx2', 'evex': 'evex.strcmp_evex',
               'evex512': 'evex512.strcmp_evex', 'evex512r': 'evex512r.strcmp_evex'},
    'strcpy': {'sse2': 'sse2.strcpy_sse2', 'sse42': 'sse42.strcpy_sse2', 'avx2': 'avx2.strcpy_avx2', 'evex': 'evex.strcpy_evex',
               'evex512': 'evex512.strcpy_evex', 'evex512r': 'evex512r.strcpy_evex'},
    'strcpy_long': {'sse2': 'sse2.strcpy_long_sse2', 'sse42': 'sse42.strcpy_long_sse2', 'avx2': 'avx2.strcpy_long_avx2',
                    'evex': 'evex.strcpy_long_avx2', 'evex512': 'evex512.strcpy_long_evex512', 'evex512r': 'evex512r.strcpy_long_evex512'},
    'strcat': {'sse2': 'sse2.strcat_sse2', 'sse42': 'sse42.strcat_sse2', 'avx2': 'avx2.strcat_avx2', 'evex': 'evex.strcat_evex',
               'evex512': 'evex512.strcat_evex', 'evex512r': 'evex512r.strcat_evex'},
    'strspn': {'sse2': 'sse2.strspn_sse2', 'sse42': 'sse42.strspn_sse42', 'avx2': 'avx2.strspn_avx2', 'evex': 'evex.strspn_avx2',
               'evex512': 'evex512.strspn_avx2_zen', 'evex512r': 'evex512r.strspn_avx2_zen'},
    'strcspn': {'sse2': 'sse2.strcspn_sse2', 'sse42': 'sse42.strcspn_sse42', 'avx2': 'avx2.strcspn_avx2', 'evex': 'evex.strcspn_avx2',
                'evex512': 'evex512.strcspn_avx2_zen', 'evex512r': 'evex512r.strcspn_avx2_zen'},
}
# the long sets (more than 32 bytes) of the same functions, a spec of their own
FUNCS['strspn_long'] = dict(FUNCS['strspn'])
FUNCS['strcspn_long'] = dict(FUNCS['strcspn'])

# (spec, config) -> classes: those not proved in one class (it did not end
# in hours, or failed where the classes do not: a shuffle by s & 15)
SPLIT = {
    **{('strcpy', c): 64 for c in ('avx2', 'evex', 'evex512', 'evex512r')},
    **{('memchr', c): 64 for c in ('avx2', 'evex', 'evex512')},
    **{(s, c): 16 for s in ('strspn', 'strcspn') for c in ('avx2', 'evex', 'evex512')},
    ('strcspn', 'evex512r'): 16,
}

# (spec, config) -> the configuration whose copy has the same code
SAME = {
    **{(s, 'sse42'): 'sse2' for s in ('strlen', 'memchr', 'memset', 'memcpy', 'memmove', 'memcmp', 'strcmp', 'strcpy', 'strcpy_long', 'strcat')},
    **{(s, 'evex512r'): 'evex512' for s in ('memchr', 'memcpy', 'memmove', 'memcmp', 'strcmp', 'strspn', 'strspn_long')},
}

DEPS = {'strcat': ('strlen', 'strcpy', 'strcpy_long')}


def name(spec, config, k=None):
    return '%s_%s' % (spec, config) if k is None else '%s_%s_%d' % (spec, config, k)
