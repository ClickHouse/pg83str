/*
 * pcmpistri.h: the SSE4.2 set scan shared by sse42.c and avx2.c: a set of up
 * to 16 bytes in one register, one pcmpistri per 16-byte text block. The AVX2
 * unit uses it for the first block (one load of the set is all most calls
 * need) and continues with the shuffle scan beyond it.
 */
#ifndef STR_PCMPISTRI_H
#define STR_PCMPISTRI_H
#include "common.h"

/* Load a NUL-terminated set of at most 16 bytes into a vector; returns 0 when
   the set is longer. Bytes after the NUL are irrelevant to pcmpistri. */
static inline int load_set16(const u8 *set, __m128i *out) {
    if (page_left(set) >= 16) {
        __m128i v = load16(set);

        if (zero_mask16(v) || set[16] == 0) {
            *out = v;
            return 1;
        }

        return 0;
    }

    u8 buffer[16] __attribute__((aligned(16))) = {0};
    size_t i = 0;

    for (; i < 16 && set[i]; i++) {
        buffer[i] = set[i];
    }

    if (i == 16 && set[16]) {
        return 0;
    }

    *out = load16a(buffer);
    return 1;
}

/* First text block for pcmpistri: 16 bytes from s when they stay in the page,
   otherwise the page remainder copied into a zero-padded buffer. *valid is
   the number of real bytes in the block. */
static inline __m128i load_head16(const u8 *s, size_t *valid) {
    if (page_left(s) >= 16) {
        *valid = 16;
        return load16(s);
    }

    u8 buffer[16] __attribute__((aligned(16))) = {0};
    size_t n = page_left(s);

    for (size_t i = 0; i < n; i++) {
        buffer[i] = s[i];
    }

    *valid = n;
    return load16a(buffer);
}

/* After the head block, continue with aligned blocks; overlapping the head is
   fine because results are relative to each block's own base. */
static inline const u8 *after_head(const u8 *s, size_t valid) {
    return valid == 16 ? align_down(s + 16, 16) : s + valid;
}

/* Equal-any with negative polarity: index of the first text byte not in the
   set; bytes after the text's NUL count as "not in set", so the NUL is found
   without a separate test. 16 means all sixteen bytes are in the set. */
#define SPN_MODE (_SIDD_UBYTE_OPS | _SIDD_CMP_EQUAL_ANY | _SIDD_NEGATIVE_POLARITY | _SIDD_LEAST_SIGNIFICANT)
/* Equal-any, positive polarity: index of the first text byte in the set, or
   16; the Z flag reports a NUL inside the text block. */
#define CSPN_MODE (_SIDD_UBYTE_OPS | _SIDD_CMP_EQUAL_ANY | _SIDD_POSITIVE_POLARITY | _SIDD_LEAST_SIGNIFICANT)

static inline unsigned cspn_index(__m128i set, __m128i text) {
    unsigned i = (unsigned)_mm_cmpistri(set, text, CSPN_MODE);

    if (i == 16 && _mm_cmpistrz(set, text, CSPN_MODE)) {
        i = ctz(zero_mask16(text)); /* no match before the NUL: stop there */
    }

    return i;
}

#endif
