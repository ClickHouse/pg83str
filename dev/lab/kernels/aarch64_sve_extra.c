/* Predicate-based comparisons and inclusive string stores. Runtime vector
   length and inactive lanes are part of the algorithm, not build constants. */
#include "aarch64_helpers.h"
#include <arm_sve.h>

extern int kl_memcmp_neon_words(const void *, const void *, size_t);
extern size_t kl_strlen_neon_unaligned(const char *);
extern size_t kl_strlen_neon_prefix32_tail128(const char *);

static inline size_t first_true(svbool_t p) {
    svbool_t all = svptrue_b8();
    return svcntp_b8(all, svbrkb_z(all, p));
}

int kl_memcmp_sve_pred1(const void *x, const void *y, size_t n) {
    const u8 *a = x, *b = y;
    svbool_t p = svwhilelt_b8_u64(0, n);
    svuint8_t u = svld1_u8(p, a), v = svld1_u8(p, b);
    svbool_t diff = svcmpne_u8(p, u, v);

    if (svptest_any(svptrue_b8(), diff)) {
        size_t i = first_true(diff);
        return (int)a[i] - b[i];
    }

    size_t vl = svcntb();
    return n > vl ? kl_memcmp_neon_words(a + vl, b + vl, n - vl) : 0;
}

int kl_memcmp_sve_pred4(const void *x, const void *y, size_t n) {
    const u8 *a = x, *b = y;
    size_t vl = svcntb();
    svbool_t p0 = svwhilelt_b8_u64(0, n), p1 = svwhilelt_b8_u64(vl, n);
    svbool_t p2 = svwhilelt_b8_u64(2 * vl, n), p3 = svwhilelt_b8_u64(3 * vl, n);
    svuint8_t a0 = svld1_u8(p0, a), b0 = svld1_u8(p0, b);
    svuint8_t a1 = svld1_u8(p1, (const u8 *)((uintptr_t)a + vl)), b1 = svld1_u8(p1, (const u8 *)((uintptr_t)b + vl));
    svuint8_t a2 = svld1_u8(p2, (const u8 *)((uintptr_t)a + 2 * vl)), b2 = svld1_u8(p2, (const u8 *)((uintptr_t)b + 2 * vl));
    svuint8_t a3 = svld1_u8(p3, (const u8 *)((uintptr_t)a + 3 * vl)), b3 = svld1_u8(p3, (const u8 *)((uintptr_t)b + 3 * vl));
    svbool_t d0 = svcmpne_u8(p0, a0, b0), d1 = svcmpne_u8(p1, a1, b1);
    svbool_t d2 = svcmpne_u8(p2, a2, b2), d3 = svcmpne_u8(p3, a3, b3);
    svbool_t all = svptrue_b8();
    svbool_t any = svorr_b_z(all, svorr_b_z(all, d0, d1), svorr_b_z(all, d2, d3));

    if (svptest_any(all, any)) {
        size_t i = svptest_any(all, d0) ? first_true(d0)
                 : svptest_any(all, d1) ? vl + first_true(d1)
                 : svptest_any(all, d2) ? 2 * vl + first_true(d2) : 3 * vl + first_true(d3);
        return (int)a[i] - b[i];
    }

    return n > 4 * vl ? kl_memcmp_neon_words(a + 4 * vl, b + 4 * vl, n - 4 * vl) : 0;
}

void *kl_memchr_sve_pred1(const void *src, int c, size_t n) {
    const u8 *s = src;
    size_t i = 0, vl = svcntb();

    for (;;) {
        svbool_t p = svwhilelt_b8_u64(i, n);
        svuint8_t v = svld1_u8(p, (const u8 *)((uintptr_t)s + i));
        svbool_t hit = svcmpeq_n_u8(p, v, (u8)c);

        if (svptest_any(svptrue_b8(), hit)) {
            return (void *)(s + i + first_true(hit));
        }

        if (n - i <= vl) {
            return 0;
        }

        i += vl;
    }
}

void *kl_memchr_sve_pred4(const void *src, int c, size_t n) {
    const u8 *s = src;
    size_t i = 0, vl = svcntb();
    svbool_t all = svptrue_b8();

    for (;;) {
        svbool_t p0 = svwhilelt_b8_u64(i, n), p1 = svwhilelt_b8_u64(i + vl, n);
        svbool_t p2 = svwhilelt_b8_u64(i + 2 * vl, n), p3 = svwhilelt_b8_u64(i + 3 * vl, n);
        svbool_t h0 = svcmpeq_n_u8(p0, svld1_u8(p0, (const u8 *)((uintptr_t)s + i)), (u8)c);
        svbool_t h1 = svcmpeq_n_u8(p1, svld1_u8(p1, (const u8 *)((uintptr_t)s + i + vl)), (u8)c);
        svbool_t h2 = svcmpeq_n_u8(p2, svld1_u8(p2, (const u8 *)((uintptr_t)s + i + 2 * vl)), (u8)c);
        svbool_t h3 = svcmpeq_n_u8(p3, svld1_u8(p3, (const u8 *)((uintptr_t)s + i + 3 * vl)), (u8)c);
        svbool_t hit = svorr_b_z(all, svorr_b_z(all, h0, h1), svorr_b_z(all, h2, h3));

        if (svptest_any(all, hit)) {
            size_t j = svptest_any(all, h0) ? first_true(h0)
                     : svptest_any(all, h1) ? vl + first_true(h1)
                     : svptest_any(all, h2) ? 2 * vl + first_true(h2) : 3 * vl + first_true(h3);
            return (void *)(s + i + j);
        }

        if (n - i <= 4 * vl) {
            return 0;
        }

        i += 4 * vl;
    }
}

static inline char *copy_predicated(char *dst, const char *src) {
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    svbool_t all = svptrue_b8();

    for (;;) {
        size_t left = 4096 - ((uintptr_t)s & 4095);
        svbool_t active = svwhilelt_b8_u64(0, left);
        svuint8_t v = svld1_u8(active, s);
        svbool_t zero = svcmpeq_n_u8(active, v, 0);
        svst1_u8(svbrka_z(active, zero), d, v);

        if (svptest_any(all, zero)) {
            return dst;
        }

        size_t count = svcntp_b8(all, active);
        s += count;
        d += count;
    }
}

char *kl_strcpy_sve_prefix(char *d, const char *s) {
    return copy_predicated(d, s);
}

char *kl_strcat_sve_compose(char *d, const char *s) {
    copy_predicated(d + kl_strlen_neon_unaligned(d), s);
    return d;
}

char *kl_strcat_sve_prefix32(char *d, const char *s) {
    copy_predicated(d + kl_strlen_neon_prefix32_tail128(d), s);
    return d;
}

static inline void finish_vector(u8 *d, const u8 *s, uint64_t bits) {
    svbool_t active = svwhilelt_b8_u64(0, kl_first(bits) + 1);
    svst1_u8(active, d, svld1_u8(active, s));
}

static __attribute__((noinline)) void copy_tail64(u8 *d, const u8 *s) {
    while ((uintptr_t)s & 63) {
        uint8x16_t v = vld1q_u8(s);
        uint64_t bits = kl_zero_syndrome(v);

        if (bits) {
            finish_vector(d, s, bits);
            return;
        }

        vst1q_u8(d, v);
        s += 16;
        d += 16;
    }

    for (;;) {
        uint8x16_t a = vld1q_u8(s), b = vld1q_u8(s + 16), c = vld1q_u8(s + 32), e = vld1q_u8(s + 48);
        uint8x16_t low = vminq_u8(vminq_u8(a, b), vminq_u8(c, e));

        if (!vminvq_u8(low)) {
            uint64_t mask = kl_zero_syndrome(a);

            if (mask) {
                finish_vector(d, s, mask);
                return;
            }

            vst1q_u8(d, a);
            mask = kl_zero_syndrome(b);

            if (mask) {
                finish_vector(d + 16, s + 16, mask);
                return;
            }

            vst1q_u8(d + 16, b);
            mask = kl_zero_syndrome(c);

            if (mask) {
                finish_vector(d + 32, s + 32, mask);
                return;
            }

            vst1q_u8(d + 32, c);
            finish_vector(d + 48, s + 48, kl_zero_syndrome(e));
            return;
        }

        vst1q_u8(d, a); vst1q_u8(d + 16, b); vst1q_u8(d + 32, c); vst1q_u8(d + 48, e);
        s += 64;
        d += 64;
    }
}

static inline char *copy_split(char *dst, const char *src) {
    const u8 *s = (const u8 *)src;
    u8 *d = (u8 *)dst;
    #pragma unroll
    for (int i = 0; i < 2; ++i) {
        svbool_t active = svwhilelt_b8_u64(0, 4096 - ((uintptr_t)s & 4095));
        svuint8_t v = svld1_u8(active, s);
        svbool_t zero = svcmpeq_n_u8(active, v, 0);
        svst1_u8(svbrka_z(active, zero), d, v);

        if (svptest_any(svptrue_b8(), zero)) {
            return dst;
        }

        size_t count = svcntp_b8(svptrue_b8(), active);
        s += count;
        d += count;
    }

    size_t skew = (uintptr_t)s & 15;
    copy_tail64(d - skew, s - skew);
    return dst;
}

char *kl_strcpy_sve_split64(char *d, const char *s) {
    return copy_split(d, s);
}

char *kl_strcat_sve_split64(char *d, const char *s) {
    copy_split(d + kl_strlen_neon_prefix32_tail128(d), s);
    return d;
}
