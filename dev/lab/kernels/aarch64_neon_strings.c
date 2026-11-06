/* First-block handling, negative scalar-prefix control, reduction trees,
   and compiler pointer independence: separate hypotheses from pg83's lab. */
#include "aarch64_helpers.h"

static inline size_t left(const void *p) {
    return 4096 - ((uintptr_t)p & 4095);
}

size_t kl_strlen_neon_aligned(const char *text) {
    const u8 *s = (const u8 *)text;
    const u8 *p = (const u8 *)((uintptr_t)s & ~(uintptr_t)15);
    uint64_t bits = kl_zero_syndrome(vld1q_u8(p)) >> (4 * ((uintptr_t)s & 15));

    if (bits) {
        return kl_first(bits);
    }

    for (;;) {
        p += 16;
        bits = kl_zero_syndrome(vld1q_u8(p));

        if (bits) {
            return (size_t)(p - s) + kl_first(bits);
        }
    }
}

size_t kl_strlen_neon_unaligned(const char *text) {
    const u8 *s = (const u8 *)text;

    if (left(s) < 16) {
        return kl_strlen_neon_aligned(text);
    }

    uint64_t bits = kl_zero_syndrome(vld1q_u8(s));

    if (bits) {
        return kl_first(bits);
    }

    const u8 *p = (const u8 *)(((uintptr_t)s & ~(uintptr_t)15) + 16);

    for (;;) {
        bits = kl_zero_syndrome(vld1q_u8(p));

        if (bits) {
            return (size_t)(p - s) + kl_first(bits);
        }

        p += 16;
    }
}

size_t kl_strlen_neon_byte0(const char *text) {
    if (!*text) {
        return 0;
    }

    return kl_strlen_neon_unaligned(text);
}

static inline int difference(const u8 *a, const u8 *b, uint64_t bits) {
    size_t index = kl_first(bits);
    return (int)a[index] - b[index];
}

int kl_strcmp_neon_block16(const char *x, const char *y) {
    const u8 *a = (const u8 *)x, *b = (const u8 *)y;

    for (;;) {
        if (left(a) >= 16 && left(b) >= 16) {
            uint64_t bits = kl_stop_syndrome(vld1q_u8(a), vld1q_u8(b));

            if (bits) {
                return difference(a, b, bits);
            }

            a += 16;
            b += 16;
        } else {
            if (!*a || *a != *b) {
                return (int)*a - *b;
            }

            ++a;
            ++b;
        }
    }
}

static inline __attribute__((always_inline)) int compare_groups(const char *x, const char *y, int tree, int opaque) {
    const u8 *a = (const u8 *)x, *b = (const u8 *)y;

    for (;;) {
        if (left(a) >= 64 && left(b) >= 64) {
            uint8x16_t u0 = vld1q_u8(a), u1 = vld1q_u8(a + 16), u2 = vld1q_u8(a + 32), u3 = vld1q_u8(a + 48);
            uint8x16_t d0 = veorq_u8(u0, vld1q_u8(b)), d1 = veorq_u8(u1, vld1q_u8(b + 16));
            uint8x16_t d2 = veorq_u8(u2, vld1q_u8(b + 32)), d3 = veorq_u8(u3, vld1q_u8(b + 48));
            uint8x16_t low, diff;

            if (tree) {
                uint8x16_t l0 = vminq_u8(u0, u1), l1 = vminq_u8(u2, u3);
                uint8x16_t p0 = vorrq_u8(d0, d1), p1 = vorrq_u8(d2, d3);

                if (opaque) {
                    __asm__("" : "+w"(l0), "+w"(l1), "+w"(p0), "+w"(p1));
                }

                low = vminq_u8(l0, l1);
                diff = vorrq_u8(p0, p1);
            } else {
                low = vminq_u8(vminq_u8(vminq_u8(u0, u1), u2), u3);
                diff = vorrq_u8(vorrq_u8(vorrq_u8(d0, d1), d2), d3);
            }

            uint8x16_t stop = vorrq_u8(diff, vceqq_u8(low, vdupq_n_u8(0)));

            if (vmaxvq_u8(stop)) {
                return kl_strcmp_neon_block16((const char *)a, (const char *)b);
            }

            a += 64;
            b += 64;

            if (opaque) {
                __asm__("" : "+r"(a), "+r"(b));
            }
        } else {
            return kl_strcmp_neon_block16((const char *)a, (const char *)b);
        }
    }
}

int kl_strcmp_neon_chain64(const char *a, const char *b) {
    return compare_groups(a, b, 0, 0);
}

int kl_strcmp_neon_tree64(const char *a, const char *b) {
    return compare_groups(a, b, 1, 0);
}

int kl_strcmp_neon_opaque64(const char *a, const char *b) {
    return compare_groups(a, b, 1, 1);
}

static __attribute__((noinline)) int compare_tail(const char *a, const char *b) {
    return compare_groups(a, b, 1, 1);
}

int kl_strcmp_neon_first16_tail64(const char *x, const char *y) {
    const u8 *a = (const u8 *)x, *b = (const u8 *)y;

    if (left(a) < 16 || left(b) < 16) {
        return kl_strcmp_neon_block16(x, y);
    }

    uint64_t bits = kl_stop_syndrome(vld1q_u8(a), vld1q_u8(b));

    if (bits) {
        return difference(a, b, bits);
    }

    return compare_tail(x + 16, y + 16);
}

int kl_strcmp_neon_seq64(const char *x, const char *y) {
    const u8 *a = (const u8 *)x, *b = (const u8 *)y;

    for (;;) {
        if (left(a) < 64 || left(b) < 64) {
            return kl_strcmp_neon_block16((const char *)a, (const char *)b);
        }
        #pragma unroll
        for (int offset = 0; offset < 64; offset += 16) {
            uint64_t bits = kl_stop_syndrome(vld1q_u8(a + offset), vld1q_u8(b + offset));

            if (bits) {
                return difference(a + offset, b + offset, bits);
            }
        }

        a += 64;
        b += 64;
    }
}

int kl_strcmp_neon_byte0(const char *a, const char *b) {
    if (!*a || (u8)*a != (u8)*b) {
        return (int)(u8)*a - (u8)*b;
    }

    return kl_strcmp_neon_block16(a, b);
}
