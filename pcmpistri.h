/*
 * pcmpistri.h: the SSE4.2 set scan shared by sse42.c and avx2.c: a set of up
 * to 16 bytes in one register, one pcmpistri per 16-byte text block. The AVX2
 * unit uses it for the first block (one load of the set is all most calls
 * need) and continues with the shuffle scan beyond it.
 */
#ifndef STR_PCMPISTRI_H
#define STR_PCMPISTRI_H
#include "common.h"

/* The bytes of the aligned 16-byte block around p, from p on, moved down to
   lane 0, the lanes past the block's end zero: a shuffle with the indexes
   i + k, those past 15 made negative (pshufb gives 0 for them). The aligned
   block never crosses a page; *valid is the number of bytes from p in it.
   No buffer on the stack: gcc copied into one byte by byte and gave the
   whole function a frame for it. */
static inline __m128i load16_from(const u8 *p, size_t *valid) {
    size_t k = (uintptr_t)p & 15;
    __m128i idx = _mm_add_epi8(_mm_setr_epi8(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15), _mm_set1_epi8((char)k));
    idx = _mm_or_si128(idx, _mm_cmpgt_epi8(idx, _mm_set1_epi8(15)));
    *valid = 16 - k;
    return _mm_shuffle_epi8(load16a(align_down(p, 16)), idx);
}

/* Load a NUL-terminated set of at most 16 bytes into a vector; returns 0 when
   the set is longer. Bytes after the NUL are irrelevant to pcmpistri. Near a
   page end: the page's bytes moved down, and if the NUL is not among them
   the set goes on in the next page (it is a string), whose first aligned
   block supplies the rest, moved up into the lanes after them. */
static inline int load_set16(const u8 *set, __m128i *out) {
    __m128i v;

    if (page_left(set) >= 16) {
        v = load16(set);
    } else {
        size_t valid;
        v = load16_from(set, &valid);

        if (!(zero_mask16(v) & ((1u << valid) - 1))) { /* the lanes past the page's bytes are zero too */
            __m128i idx = _mm_sub_epi8(_mm_setr_epi8(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15), _mm_set1_epi8((char)valid));
            v = _mm_or_si128(v, _mm_shuffle_epi8(load16a(set + valid), idx)); /* lanes before valid: negative index, 0 */
        }
    }

    if (zero_mask16(v) || set[16] == 0) {
        *out = v;
        return 1;
    }

    return 0;
}

/* First text block for pcmpistri: 16 bytes from s when they stay in the page,
   otherwise the page remainder moved down to lane 0 with zeros after it.
   *valid is the number of real bytes in the block. */
static inline __m128i load_head16(const u8 *s, size_t *valid) {
    if (page_left(s) >= 16) {
        *valid = 16;
        return load16(s);
    }

    return load16_from(s, valid);
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
