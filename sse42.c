/*
 * sse42.c: strspn and strcspn with SSE4.2, the only primitives that gain from
 * it. Compile with -march=x86-64-v2. Sets of up to 16 bytes are matched with
 * one pcmpistri per text block, sets of 17..32 with two pcmpistrm; longer
 * sets use the SSE2 table scan.
 */
#include "common.h"

/* --------------------------------------------------------- strspn/strcspn */

/* Load a NUL-terminated set of at most 16 bytes into a vector; returns 0 when
   the set is longer. Bytes after the NUL are irrelevant to pcmpistri. */
static inline int load_set16(const u8 *set, __m128i *out)
{
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
    for (; i < 16 && set[i]; i++)
        buffer[i] = set[i];
    if (i == 16 && set[16])
        return 0;
    *out = load16a(buffer);
    return 1;
}

/* First text block for pcmpistri: 16 bytes from s when they stay in the page,
   otherwise the page remainder copied into a zero-padded buffer. *valid is
   the number of real bytes in the block. */
static inline __m128i load_head16(const u8 *s, size_t *valid)
{
    if (page_left(s) >= 16) {
        *valid = 16;
        return load16(s);
    }
    u8 buffer[16] __attribute__((aligned(16))) = {0};
    size_t n = page_left(s);
    for (size_t i = 0; i < n; i++)
        buffer[i] = s[i];
    *valid = n;
    return load16a(buffer);
}

/* After the head block, continue with aligned blocks; overlapping the head is
   fine because results are relative to each block's own base. */
static inline const u8 *after_head(const u8 *s, size_t valid)
{
    return valid == 16 ? align_down(s + 16, 16) : s + valid;
}

/* Equal-any with negative polarity: index of the first text byte not in the
   set; bytes after the text's NUL count as "not in set", so the NUL is found
   without a separate test. 16 means all sixteen bytes are in the set. */
#define SPN_MODE (_SIDD_UBYTE_OPS | _SIDD_CMP_EQUAL_ANY | _SIDD_NEGATIVE_POLARITY | _SIDD_LEAST_SIGNIFICANT)
/* Equal-any, positive polarity: index of the first text byte in the set, or
   16; the Z flag reports a NUL inside the text block. */
#define CSPN_MODE (_SIDD_UBYTE_OPS | _SIDD_CMP_EQUAL_ANY | _SIDD_POSITIVE_POLARITY | _SIDD_LEAST_SIGNIFICANT)

static size_t strspn_pcmpistri(const u8 *s, __m128i set)
{
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = (unsigned)_mm_cmpistri(set, head, SPN_MODE);
    if (i < valid)
        return i;
    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = (unsigned)_mm_cmpistri(set, load16a(p), SPN_MODE);
        if (i < 16)
            return (size_t)(p - s) + i;
    }
}

static inline unsigned cspn_index(__m128i set, __m128i text)
{
    unsigned i = (unsigned)_mm_cmpistri(set, text, CSPN_MODE);
    if (i == 16 && _mm_cmpistrz(set, text, CSPN_MODE))
        i = ctz(zero_mask16(text)); /* no match before the NUL: stop there */
    return i;
}

static size_t strcspn_pcmpistri(const u8 *s, __m128i set)
{
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = cspn_index(set, head);
    if (i < valid)
        return i;
    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = cspn_index(set, load16a(p));
        if (i < 16)
            return (size_t)(p - s) + i;
    }
}

/* Second 16 bytes of a set longer than 16; returns 0 when the set is longer
   than 32. */
static inline int load_set_second16(const u8 *set, __m128i *out)
{
    const u8 *rest = set + 16;
    if (page_left(rest) >= 16) {
        __m128i v = load16(rest);
        if (zero_mask16(v) || rest[16] == 0) {
            *out = v;
            return 1;
        }
        return 0;
    }
    u8 buffer[16] __attribute__((aligned(16))) = {0};
    size_t i = 0;
    for (; i < 16 && rest[i]; i++)
        buffer[i] = rest[i];
    if (i == 16 && rest[16])
        return 0;
    *out = load16a(buffer);
    return 1;
}

#define SPN_MASK_MODE (SPN_MODE | _SIDD_BIT_MASK)
#define CSPN_MASK_MODE (CSPN_MODE | _SIDD_BIT_MASK)

/* Two set halves: a byte is outside the set when it is outside both halves. */
static inline unsigned spn2_index(__m128i set1, __m128i set2, __m128i text)
{
    unsigned m = (unsigned)_mm_cvtsi128_si32(_mm_cmpistrm(set1, text, SPN_MASK_MODE))
               & (unsigned)_mm_cvtsi128_si32(_mm_cmpistrm(set2, text, SPN_MASK_MODE));
    return m ? ctz(m) : 16;
}

static size_t strspn_pcmpistrm32(const u8 *s, __m128i set1, __m128i set2)
{
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = spn2_index(set1, set2, head);
    if (i < valid)
        return i;
    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = spn2_index(set1, set2, load16a(p));
        if (i < 16)
            return (size_t)(p - s) + i;
    }
}

/* A byte is in the set when it is in either half; a NUL with no earlier match
   stops the scan. */
static inline unsigned cspn2_index(__m128i set1, __m128i set2, __m128i text)
{
    unsigned m = (unsigned)_mm_cvtsi128_si32(_mm_cmpistrm(set1, text, CSPN_MASK_MODE))
               | (unsigned)_mm_cvtsi128_si32(_mm_cmpistrm(set2, text, CSPN_MASK_MODE));
    if (m)
        return ctz(m);
    if (_mm_cmpistrz(set1, text, CSPN_MASK_MODE))
        return ctz(zero_mask16(text));
    return 16;
}

static size_t strcspn_pcmpistrm32(const u8 *s, __m128i set1, __m128i set2)
{
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = cspn2_index(set1, set2, head);
    if (i < valid)
        return i;
    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = cspn2_index(set1, set2, load16a(p));
        if (i < 16)
            return (size_t)(p - s) + i;
    }
}

size_t strspn_sse42(const char *str, const char *accept)
{
    const u8 *s = (const u8 *)str, *set = (const u8 *)accept;
    __m128i set1, set2;
    if (!set[0])
        return 0;
    if (load_set16(set, &set1))
        return strspn_pcmpistri(s, set1);
    set1 = load16(set); /* 16 non-NUL bytes: page-safe, see load_set16 */
    if (load_set_second16(set, &set2))
        return strspn_pcmpistrm32(s, set1, set2);
    return strspn_table_sse2(s, set);
}

size_t strcspn_sse42(const char *str, const char *reject)
{
    const u8 *s = (const u8 *)str, *set = (const u8 *)reject;
    __m128i set1, set2;
    if (!set[0])
        return str_strlen(str);
    if (load_set16(set, &set1))
        return strcspn_pcmpistri(s, set1);
    set1 = load16(set);
    if (load_set_second16(set, &set2))
        return strcspn_pcmpistrm32(s, set1, set2);
    return strcspn_table_sse2(s, set);
}

