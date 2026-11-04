/*
 * memset: a masked 64-byte store up to a threshold, rep stosb beyond. The
 * masked store has no size branch below 64 bytes; rep stosb has none at all
 * above, and on Zen 5 its start is short (see sse2_memset_rep.c).
 */
#include <immintrin.h>
#include <stddef.h>
#include <stdint.h>

static inline void *rep_fill(void *d, int c, size_t n) {
    void *p = d;
    __asm__ volatile("rep stosb" : "+D"(p), "+c"(n) : "a"(c) : "memory");
    return d;
}

void *kl_memset_evex512_rep64(void *d, int c, size_t n) {
    if (n <= 64) {
        __mmask64 k = _bzhi_u64(~0ull, (unsigned)n);
        _mm512_mask_storeu_epi8(d, k, _mm512_set1_epi8((char)c));
        return d;
    }

    return rep_fill(d, c, n);
}

void *kl_memset_evex512_rep128(void *d, int c, size_t n) {
    __m512i v = _mm512_set1_epi8((char)c);

    if (n <= 64) {
        __mmask64 k = _bzhi_u64(~0ull, (unsigned)n);
        _mm512_mask_storeu_epi8(d, k, v);
        return d;
    }

    if (n <= 128) {
        _mm512_storeu_si512(d, v);
        _mm512_storeu_si512((char *)d + n - 64, v);
        return d;
    }

    return rep_fill(d, c, n);
}

void *kl_memset_evex512_rep256(void *d, int c, size_t n) {
    __m512i v = _mm512_set1_epi8((char)c);

    if (n <= 64) {
        __mmask64 k = _bzhi_u64(~0ull, (unsigned)n);
        _mm512_mask_storeu_epi8(d, k, v);
        return d;
    }

    if (n <= 256) {
        char *e = (char *)d + n;
        _mm512_storeu_si512(d, v);
        _mm512_storeu_si512(e - 64, v);

        if (n > 128) {
            _mm512_storeu_si512((char *)d + 64, v);
            _mm512_storeu_si512(e - 128, v);
        }

        return d;
    }

    return rep_fill(d, c, n);
}
