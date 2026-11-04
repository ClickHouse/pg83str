/*
 * Short strings by scalar words or bytes first. A vector block costs a
 * load, a compare and the move of its mask to a general register before
 * the answer (12 to 15 cycles on Zen 5); a 64-bit word and the borrow trick
 * (x - 0x01..) & ~x & 0x80.. answer in about 7, and in the latency regime a
 * byte loop beats every vector strlen and strcmp up to a few bytes by 2x.
 *   swar8   one word when it stays in the page, then the aligned 32-byte
 *           block around s with the bytes before s shifted out;
 *   swar16  two words;
 *   aligned no word: the aligned block with the shift, no page test at all;
 *   strcmp  one word of each string, then 32-byte blocks;
 *   byte0   hybrids: the first byte (strcmp: of both strings) by itself,
 *           then our dispatched implementation through str_*.
 * Result (Zen 5, 2026-11-04): the latency regime at a fixed size wins up to
 * 3x (strcmp differing in the first byte: 1.7 ns against 5.2), the workload
 * traces lose 1.4x to 3.4x (strcmp byte0 5.6 ns against ours 2.4, strlen
 * swar8 8.3 against 2.4): with sizes and contents drawn from the workload the
 * scalar exit's branch goes either way and mispredicts, while the vector path
 * takes the same branch for nearly every string under 32 bytes. Kept as the
 * record: a prefix in front of the vector code has to be branch-free.
 */
#include <immintrin.h>
#include <stdint.h>

#include "common.h"

static inline __m256i ld32a(const void *p) {
    return _mm256_load_si256((const __m256i *)p);
}

static inline __m256i ld32(const void *p) {
    return _mm256_loadu_si256((const __m256i *)p);
}

static inline unsigned zmask32(__m256i v) {
    return (unsigned)_mm256_movemask_epi8(_mm256_cmpeq_epi8(v, _mm256_setzero_si256()));
}

static inline uint64_t zero_bytes(uint64_t x) {
    return (x - 0x0101010101010101ull) & ~x & 0x8080808080808080ull; /* the lowest set bit is exact */
}

/* From the aligned 32-byte block around s on; no NUL before s matters. */
static size_t len_from(const u8 *s) {
    const u8 *a = align_down(s, 32);
    unsigned m = zmask32(ld32a(a)) >> (s - a);

    if (m) {
        return ctz(m);
    }

    /* single blocks to a 128-byte boundary: a group of four never crosses a page */
    for (a += 32; (uintptr_t)a & 127; a += 32) {
        m = zmask32(ld32a(a));

        if (m) {
            return (size_t)(a - s) + ctz(m);
        }
    }

    for (;;) {
        __m256i x0 = ld32a(a), x1 = ld32a(a + 32), x2 = ld32a(a + 64), x3 = ld32a(a + 96);
        __m256i lo = _mm256_min_epu8(_mm256_min_epu8(x0, x1), _mm256_min_epu8(x2, x3));

        if (zmask32(lo)) {
            uint64_t m01 = zmask32(x0) | (uint64_t)zmask32(x1) << 32, m23 = zmask32(x2) | (uint64_t)zmask32(x3) << 32;
            return (size_t)(a - s) + (m01 ? (size_t)__builtin_ctzll(m01) : 64 + (size_t)__builtin_ctzll(m23));
        }

        a += 128;
    }
}

size_t kl_strlen_avx2_swar8(const char *str) {
    const u8 *s = (const u8 *)str;

    if (page_left(s) >= 8) {
        uint64_t z = zero_bytes(load64(s));

        if (z) {
            return (size_t)__builtin_ctzll(z) >> 3;
        }
    }

    return len_from(s);
}

size_t kl_strlen_avx2_swar16(const char *str) {
    const u8 *s = (const u8 *)str;

    if (page_left(s) >= 16) {
        uint64_t z = zero_bytes(load64(s));

        if (z) {
            return (size_t)__builtin_ctzll(z) >> 3;
        }

        z = zero_bytes(load64(s + 8));

        if (z) {
            return 8 + ((size_t)__builtin_ctzll(z) >> 3);
        }
    }

    return len_from(s);
}

size_t kl_strlen_avx2_aligned(const char *str) {
    return len_from((const u8 *)str);
}

/* strcmp: a word of each string when both stay in their pages: the bytes
   that differ (x ^ y) or end (zero_bytes of x), the lowest one decides. */
int kl_strcmp_avx2_swar8(const char *left, const char *right) {
    const u8 *a = (const u8 *)left, *b = (const u8 *)right;

    if (page_left(a) >= 8 && page_left(b) >= 8) {
        uint64_t x = load64(a), y = load64(b), stop = zero_bytes(x) | (x ^ y);

        if (stop) {
            size_t i = (size_t)__builtin_ctzll(stop) >> 3;
            return a[i] - b[i];
        }

        a += 8;
        b += 8;
    }

    for (;;) {
        if (page_left(a) >= 32 && page_left(b) >= 32) {
            __m256i x = ld32(a), y = ld32(b);
            unsigned m = (unsigned)_mm256_movemask_epi8(_mm256_or_si256(_mm256_cmpeq_epi8(x, _mm256_setzero_si256()),
                                                                         _mm256_xor_si256(_mm256_cmpeq_epi8(x, y), _mm256_set1_epi8(-1))));

            if (m) {
                return a[ctz(m)] - b[ctz(m)];
            }

            a += 32;
            b += 32;
        } else {
            if (*a != *b || !*a) {
                return *a - *b;
            }

            a++;
            b++;
        }
    }
}

/* Hybrids: the first bytes scalar, then our dispatched code. */
int kl_strcmp_avx2_byte0_ours(const char *left, const char *right) {
    const u8 *a = (const u8 *)left, *b = (const u8 *)right;

    if (a[0] != b[0] || !a[0]) {
        return a[0] - b[0];
    }

    return str_strcmp(left, right);
}

int kl_strcmp_avx2_byte01_ours(const char *left, const char *right) {
    const u8 *a = (const u8 *)left, *b = (const u8 *)right;

    if (a[0] != b[0] || !a[0]) {
        return a[0] - b[0];
    }

    if (a[1] != b[1] || !a[1]) {
        return a[1] - b[1];
    }

    return str_strcmp(left, right);
}

size_t kl_strlen_avx2_swar8_ours(const char *str) {
    const u8 *s = (const u8 *)str;

    if (page_left(s) >= 8) {
        uint64_t z = zero_bytes(load64(s));

        if (z) {
            return (size_t)__builtin_ctzll(z) >> 3;
        }
    }

    return str_strlen(str);
}

size_t kl_strlen_avx2_byte0_ours(const char *str) {
    if (!str[0]) {
        return 0;
    }

    return str_strlen(str);
}
