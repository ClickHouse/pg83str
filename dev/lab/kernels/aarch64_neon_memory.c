/* Experiments from pg83's size-dispatch and store-order investigations.
   Keep variants side by side: fixed-size wins must survive mixed traces. */
#include "aarch64_helpers.h"

void *kl_memcpy_neon_tier(void *d, const void *s, size_t n) {
    if (kl_copy_tier(d, s, n)) {
        return d;
    }

    return memcpy_neon(d, s, n);
}

void *kl_memmove_neon_tier(void *d, const void *s, size_t n) {
    if (kl_copy_tier(d, s, n)) {
        return d;
    }

    return memmove_neon(d, s, n);
}

typedef uint8x16_t unaligned_vec __attribute__((aligned(1), may_alias));

static inline __attribute__((always_inline)) void ordered_store(u8 *d, uint8x16_t v, int ordered) {
    if (ordered) {
        /* Keep one store instruction per vector and preserve ordering for the
           controlled experiment. The plain C variants may be canonicalized. */
        __asm__ volatile("str %q1, %0" : "=m"(*(unaligned_vec *)d) : "w"(v) : "memory");
    } else {
        vst1q_u8(d, v);
    }
}

static inline __attribute__((always_inline)) void *copy_order(void *dst, const void *src, size_t n, int order, int ordered) {
    u8 *d = dst;
    const u8 *s = src;

    if (kl_copy_tier(d, s, n)) {
        return dst;
    }

    if (n > 256) {
        return memcpy_neon(dst, src, n);
    }

    uint8x16_t head[8], tail[8];
    #pragma unroll
    for (int i = 0; i < 8; ++i) {
        head[i] = vld1q_u8(s + 16 * i);
        tail[i] = vld1q_u8(s + n - 128 + 16 * i);
    }
    #pragma unroll
    for (int i = 0; i < 8; ++i) {
        ordered_store(d + 16 * i, head[i], ordered);

        if (order == 2) {
            ordered_store(d + n - 16 - 16 * i, tail[7 - i], ordered);
        }
    }

    if (order != 2) {
        #pragma unroll
        for (int i = 0; i < 8; ++i) {
            int j = order == 1 ? 7 - i : i;
            ordered_store(d + n - 128 + 16 * j, tail[j], ordered);
        }
    }

    return dst;
}

void *kl_memcpy_neon_asc(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 0, 0);
}

void *kl_memcpy_neon_desc(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 1, 0);
}

void *kl_memcpy_neon_alt(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 2, 0);
}

void *kl_memcpy_neon_ordered_asc(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 0, 1);
}

void *kl_memcpy_neon_ordered_desc(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 1, 1);
}

void *kl_memcpy_neon_ordered_alt(void *d, const void *s, size_t n) {
    return copy_order(d, s, n, 2, 1);
}

static inline void fill_words(u8 *d, int c, size_t n) {
    uint64_t q = (u8)c * UINT64_C(0x0101010101010101);

    if (n >= 8) {
        kl_store64(d, q);
        kl_store64(d + n - 8, q);
    } else if (n >= 4) {
        kl_store32(d, (uint32_t)q);
        kl_store32(d + n - 4, (uint32_t)q);
    } else if (n) {
        d[0] = (u8)c;
        d[n / 2] = (u8)c;
        d[n - 1] = (u8)c;
    }
}

static inline void fill_vectors(u8 *d, int c, size_t n) {
    uint8x16_t v = vdupq_n_u8((u8)c);

    if (n <= 32) {
        vst1q_u8(d, v); vst1q_u8(d + n - 16, v);
    } else if (n <= 64) {
        vst1q_u8(d, v); vst1q_u8(d + 16, v);
        vst1q_u8(d + n - 32, v); vst1q_u8(d + n - 16, v);
    } else {
        vst1q_u8(d, v); vst1q_u8(d + 16, v); vst1q_u8(d + 32, v); vst1q_u8(d + 48, v);
        vst1q_u8(d + n - 64, v); vst1q_u8(d + n - 48, v); vst1q_u8(d + n - 32, v); vst1q_u8(d + n - 16, v);
    }
}

void *kl_memset_neon_tier(void *dst, int c, size_t n) {
    if (n < 16) {
        fill_words(dst, c, n);
    } else if (n <= 128) {
        fill_vectors(dst, c, n);
    } else {
        return memset_neon(dst, c, n);
    }

    return dst;
}

void *kl_memset_neon_flat(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (!n) {
        return dst;
    }

    if (n == 1) {
        d[0] = (u8)c;
        return dst;
    }

    if (n <= 8) {
        fill_words(d, c, n);
    } else if (n <= 16) {
        uint64_t q = (u8)c * UINT64_C(0x0101010101010101);
        kl_store64(d, q); kl_store64(d + n - 8, q);
    } else if (n <= 128) {
        fill_vectors(d, c, n);
    } else {
        return memset_neon(dst, c, n);
    }

    return dst;
}
