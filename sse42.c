/*
 * sse42.c: strspn and strcspn with SSE4.2, the only primitives that gain from
 * it. Compile with -march=x86-64-v2. Sets of up to 16 bytes are matched with
 * one pcmpistri per text block, sets of 17..32 with two pcmpistrm; longer
 * sets use the SSE2 table scan.
 */
#include "common.h"
#include "pcmpistri.h"

/* --------------------------------------------------------- strspn/strcspn */

static size_t strspn_pcmpistri(const u8 *s, __m128i set) {
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = (unsigned)_mm_cmpistri(set, head, SPN_MODE);

    if (i < valid) {
        return i;
    }

    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = (unsigned)_mm_cmpistri(set, load16a(p), SPN_MODE);

        if (i < 16) {
            return (size_t)(p - s) + i;
        }
    }
}

static size_t strcspn_pcmpistri(const u8 *s, __m128i set) {
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = cspn_index(set, head);

    if (i < valid) {
        return i;
    }

    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = cspn_index(set, load16a(p));

        if (i < 16) {
            return (size_t)(p - s) + i;
        }
    }
}

static size_t strspn_pcmpistrm32(const u8 *s, __m128i set1, __m128i set2) {
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned m = spn2_misses(set1, set2, head);

    if (m && ctz(m) < valid) {
        return ctz(m);
    }

    for (const u8 *p = after_head(s, valid);; p += 16) {
        m = spn2_misses(set1, set2, load16a(p));

        if (m) {
            return (size_t)(p - s) + ctz(m);
        }
    }
}

static size_t strcspn_pcmpistrm32(const u8 *s, __m128i set1, __m128i set2) {
    size_t valid;
    __m128i head = load_head16(s, &valid);
    unsigned i = cspn2_index(set1, set2, head);

    if (i < valid) {
        return i;
    }

    for (const u8 *p = after_head(s, valid);; p += 16) {
        i = cspn2_index(set1, set2, load16a(p));

        if (i < 16) {
            return (size_t)(p - s) + i;
        }
    }
}

size_t strspn_sse42(const char *str, const char *accept) {
    const u8 *s = (const u8 *)str, *set = (const u8 *)accept;
    __m128i set1, set2;

    if (!set[0]) {
        return 0;
    }

    if (load_set16(set, &set1)) {
        return strspn_pcmpistri(s, set1);
    }

    set1 = load16(set); /* 16 non-NUL bytes: page-safe, see load_set16 */

    if (load_set_second16(set, &set2)) {
        return strspn_pcmpistrm32(s, set1, set2);
    }

    return strspn_table_sse2(s, set);
}

size_t strcspn_sse42(const char *str, const char *reject) {
    const u8 *s = (const u8 *)str, *set = (const u8 *)reject;
    __m128i set1, set2;

    if (!set[0]) {
        return str_strlen(str);
    }

    if (load_set16(set, &set1)) {
        return strcspn_pcmpistri(s, set1);
    }

    set1 = load16(set);

    if (load_set_second16(set, &set2)) {
        return strcspn_pcmpistrm32(s, set1, set2);
    }

    return strcspn_table_sse2(s, set);
}
