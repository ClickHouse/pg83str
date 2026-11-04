/*
 * memset's size dispatch on the workload's sizes (log-normal, median 29):
 * on Broadwell LLVM libc's memset, slower than ours in every fixed-size cell
 * from 17 to 128 bytes, ran the memset trace 15% faster (r3). Variants:
 *   llvm    its order: == 0, == 1, == 2, == 3, <= 8, <= 16, <= 32, <= 64,
 *           <= 128, the value spread by a multiply below 16 bytes;
 *   gpr     ours (a split at 32, then >= 16, >= 8, >= 4, >= 2), the value
 *           spread by a multiply instead of moved out of a vector;
 *   flat    ours with 33..64 and 65..128 as paths of their own (glibc);
 *   jump    a table of the paths by the highest bit of n: one indirect
 *           branch instead of a tree.
 * Beyond 128 bytes all go to str_fill_large.
 */
#include <immintrin.h>
#include <stdint.h>
#include <string.h>

#include "common.h"

static inline void st64(void *p, uint64_t v) {
    memcpy(p, &v, 8);
}

static inline void st32(void *p, uint32_t v) {
    memcpy(p, &v, 4);
}

static inline void st16(void *p, uint16_t v) {
    memcpy(p, &v, 2);
}

static inline void sx(void *p, __m128i v) {
    _mm_storeu_si128((__m128i *)p, v);
}

static inline void sy(void *p, __m256i v) {
    _mm256_storeu_si256((__m256i *)p, v);
}

void *kl_memset_avx2_llvm(void *dst, int c, size_t n) {
    u8 *d = dst;
    uint64_t q = (u8)c * 0x0101010101010101ull;

    if (n == 0) {
        return dst;
    }

    if (n == 1) {
        d[0] = (u8)c;
        return dst;
    }

    if (n == 2) {
        st16(d, (uint16_t)q);
        return dst;
    }

    if (n == 3) {
        st16(d, (uint16_t)q);
        d[2] = (u8)c;
        return dst;
    }

    if (n <= 8) {
        st32(d, (uint32_t)q);
        st32(d + n - 4, (uint32_t)q);
        return dst;
    }

    if (n <= 16) {
        st64(d, q);
        st64(d + n - 8, q);
        return dst;
    }

    if (n <= 32) {
        __m128i v = _mm_set1_epi8((char)c);
        sx(d, v);
        sx(d + n - 16, v);
        return dst;
    }

    __m256i v = _mm256_set1_epi8((char)c);

    if (n <= 64) {
        sy(d, v);
        sy(d + n - 32, v);
        return dst;
    }

    if (n <= 128) {
        sy(d, v);
        sy(d + 32, v);
        sy(d + n - 64, v);
        sy(d + n - 32, v);
        return dst;
    }

    return str_fill_large(d, c, n);
}

void *kl_memset_avx2_gpr(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 32) {
        uint64_t q = (u8)c * 0x0101010101010101ull;

        if (n >= 16) {
            __m128i v = _mm_set1_epi8((char)c);
            sx(d, v);
            sx(d + n - 16, v);
        } else if (n >= 8) {
            st64(d, q);
            st64(d + n - 8, q);
        } else if (n >= 4) {
            st32(d, (uint32_t)q);
            st32(d + n - 4, (uint32_t)q);
        } else if (n >= 2) {
            st16(d, (uint16_t)q);
            d[n - 1] = (u8)c;
        } else if (n) {
            d[0] = (u8)c;
        }

        return dst;
    }

    if (n <= 128) {
        __m256i v = _mm256_set1_epi8((char)c);
        sy(d, v);
        sy(d + n - 32, v);

        if (n > 64) {
            sy(d + 32, v);
            sy(d + n - 64, v);
        }

        return dst;
    }

    return str_fill_large(d, c, n);
}

void *kl_memset_avx2_flat(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 32) {
        uint64_t q = (u8)c * 0x0101010101010101ull;

        if (n >= 16) {
            __m128i v = _mm_set1_epi8((char)c);
            sx(d, v);
            sx(d + n - 16, v);
        } else if (n >= 8) {
            st64(d, q);
            st64(d + n - 8, q);
        } else if (n >= 4) {
            st32(d, (uint32_t)q);
            st32(d + n - 4, (uint32_t)q);
        } else if (n >= 2) {
            st16(d, (uint16_t)q);
            d[n - 1] = (u8)c;
        } else if (n) {
            d[0] = (u8)c;
        }

        return dst;
    }

    __m256i v = _mm256_set1_epi8((char)c);

    if (n <= 64) {
        sy(d, v);
        sy(d + n - 32, v);
        return dst;
    }

    if (n <= 128) {
        sy(d, v);
        sy(d + 32, v);
        sy(d + n - 64, v);
        sy(d + n - 32, v);
        return dst;
    }

    return str_fill_large(d, c, n);
}

/* n's highest bit picks the path: 0 (n == 0), 1, 2..3, 4..7, 8..15, 16..31,
   32..63, 64..127, and the rest. */
void *kl_memset_avx2_jump(void *dst, int c, size_t n) {
    static const void *const go[] = {&&z, &&b1, &&b2, &&b4, &&b8, &&b16, &&b32, &&b64};
    u8 *d = dst;
    uint64_t q = (u8)c * 0x0101010101010101ull;
    __m256i v;
    unsigned k = n ? 64 - (unsigned)__builtin_clzll(n) : 0; /* 1 + the highest bit */

    if (k > 7) {
        return str_fill_large(d, c, n);
    }

    goto *go[k];
b1:
    d[0] = (u8)c;
z:
    return dst;
b2:
    st16(d, (uint16_t)q);
    d[n - 1] = (u8)c;
    return dst;
b4:
    st32(d, (uint32_t)q);
    st32(d + n - 4, (uint32_t)q);
    return dst;
b8:
    st64(d, q);
    st64(d + n - 8, q);
    return dst;
b16:
    sx(d, _mm_set1_epi8((char)c));
    sx(d + n - 16, _mm_set1_epi8((char)c));
    return dst;
b32:
    v = _mm256_set1_epi8((char)c);
    sy(d, v);
    sy(d + n - 32, v);
    return dst;
b64:
    v = _mm256_set1_epi8((char)c);
    sy(d, v);
    sy(d + 32, v);
    sy(d + n - 64, v);
    sy(d + n - 32, v);
    return dst;
}

/* Ours up to 128 bytes, the path beyond with fewer stores: one unaligned
   store at the start, aligned ones to the end, one unaligned at the end
   (LLVM's shape). Ours writes four unaligned stores at each end around the
   aligned loop: 8 stores, some across lines, for 129..256 bytes; Broadwell
   has one store port, and in the trace the lines are not in L1. */
static inline void *fill_few(u8 *d, __m256i v, size_t n) {
    u8 *end = d + n, *a = (u8 *)align_down(d + 32, 32);
    sy(d, v);

    for (; a + 128 <= end; a += 128) {
        _mm256_store_si256((__m256i *)a, v);
        _mm256_store_si256((__m256i *)(a + 32), v);
        _mm256_store_si256((__m256i *)(a + 64), v);
        _mm256_store_si256((__m256i *)(a + 96), v);
    }

    for (; a + 32 <= end; a += 32) {
        _mm256_store_si256((__m256i *)a, v);
    }

    sy(end - 32, v);
    return d;
}

void *kl_memset_avx2_few(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 32) {
        uint64_t q = (u8)c * 0x0101010101010101ull;

        if (n >= 16) {
            __m128i v = _mm_set1_epi8((char)c);
            sx(d, v);
            sx(d + n - 16, v);
        } else if (n >= 8) {
            st64(d, q);
            st64(d + n - 8, q);
        } else if (n >= 4) {
            st32(d, (uint32_t)q);
            st32(d + n - 4, (uint32_t)q);
        } else if (n >= 2) {
            st16(d, (uint16_t)q);
            d[n - 1] = (u8)c;
        } else if (n) {
            d[0] = (u8)c;
        }

        return dst;
    }

    __m256i v = _mm256_set1_epi8((char)c);

    if (n <= 128) {
        sy(d, v);
        sy(d + n - 32, v);

        if (n > 64) {
            sy(d + 32, v);
            sy(d + n - 64, v);
        }

        return dst;
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    return fill_few(d, v, n);
}

/* The same with the 32-byte steps after the 128-byte ones as an unrolled tail. */
void *kl_memset_avx2_few2(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 128) {
        return kl_memset_avx2_few(dst, c, n);
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    __m256i v = _mm256_set1_epi8((char)c);
    u8 *end = d + n, *a = (u8 *)align_down(d + 32, 32);
    sy(d, v);

    for (; a + 128 <= end; a += 128) {
        _mm256_store_si256((__m256i *)a, v);
        _mm256_store_si256((__m256i *)(a + 32), v);
        _mm256_store_si256((__m256i *)(a + 64), v);
        _mm256_store_si256((__m256i *)(a + 96), v);
    }

    /* 0..3 aligned blocks left, then the end */
    if (a + 32 <= end) {
        _mm256_store_si256((__m256i *)a, v);

        if (a + 64 <= end) {
            _mm256_store_si256((__m256i *)(a + 32), v);

            if (a + 96 <= end) {
                _mm256_store_si256((__m256i *)(a + 64), v);
            }
        }
    }

    sy(end - 32, v);
    return dst;
}
