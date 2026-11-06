/* Transfer pg83's masked first-block idea to SVE, with 1/2/4 vectors.
   These use the OS-selected vector length; inactive lanes never access memory.
   They are lab candidates, not certified by the current ARM proof backend. */
#include "aarch64_helpers.h"
#include <arm_sve.h>

extern void *kl_memcpy_neon_tier(void *, const void *, size_t);
extern void *kl_memset_neon_tier(void *, int, size_t);

static __attribute__((noinline)) void *copy_after(void *dst, const void *src, size_t n, size_t prefix) {
    u8 *d = dst;
    const u8 *s = src;
    size_t i = prefix;

    while (n - i >= 16) {
        vst1q_u8(d + i, vld1q_u8(s + i));
        i += 16;
    }

    while (i < n) {
        d[i] = s[i];
        ++i;
    }

    return dst;
}

static inline __attribute__((always_inline)) void *copy_masked(void *dst, const void *src, size_t n, unsigned blocks) {
    size_t vl = svcntb();
    svbool_t p0 = svwhilelt_b8_u64(0, n);
    svuint8_t a = svld1_u8(p0, src);
    svbool_t p1 = svwhilelt_b8_u64(vl, n);
    svbool_t p2 = svwhilelt_b8_u64(2 * vl, n);
    svbool_t p3 = svwhilelt_b8_u64(3 * vl, n);
    svuint8_t b = svdup_n_u8(0), c = b, e = b;

    if (blocks >= 2) {
        b = svld1_u8(p1, (const u8 *)((uintptr_t)src + vl));
    }

    if (blocks >= 4) {
        c = svld1_u8(p2, (const u8 *)((uintptr_t)src + 2 * vl));
        e = svld1_u8(p3, (const u8 *)((uintptr_t)src + 3 * vl));
    }

    svst1_u8(p0, dst, a);

    if (blocks >= 2) {
        svst1_u8(p1, (u8 *)((uintptr_t)dst + vl), b);
    }

    if (blocks >= 4) {
        svst1_u8(p2, (u8 *)((uintptr_t)dst + 2 * vl), c);
        svst1_u8(p3, (u8 *)((uintptr_t)dst + 3 * vl), e);
    }

    if (n <= blocks * vl) {
        return dst;
    }

    return copy_after(dst, src, n, blocks * vl);
}

void *kl_memcpy_sve_pred1(void *d, const void *s, size_t n) {
    return copy_masked(d, s, n, 1);
}

void *kl_memcpy_sve_pred2(void *d, const void *s, size_t n) {
    return copy_masked(d, s, n, 2);
}

void *kl_memcpy_sve_pred4(void *d, const void *s, size_t n) {
    return copy_masked(d, s, n, 4);
}

void *kl_memcpy_sve_gate4vl(void *d, const void *s, size_t n) {
    if (n > 4 * svcntb()) {
        return kl_memcpy_neon_tier(d, s, n);
    }

    return copy_masked(d, s, n, 4);
}

void *kl_memcpy_sve_hybrid4vl(void *d, const void *s, size_t n) {
    size_t prefix = 4 * svcntb();
    copy_masked(d, s, n < prefix ? n : prefix, 4);

    if (n > prefix) {
        return kl_memcpy_neon_tier(d, s, n);
    }

    return d;
}

void *kl_memmove_sve_pred1(void *d, const void *s, size_t n) {
    if (n > svcntb()) {
        return memmove_neon(d, s, n);
    }

    return copy_masked(d, s, n, 1);
}

void *kl_memmove_sve_pred4(void *d, const void *s, size_t n) {
    if (n > 4 * svcntb()) {
        return memmove_neon(d, s, n);
    }

    return copy_masked(d, s, n, 4);
}

static __attribute__((noinline)) void *fill_after(void *dst, int c, size_t n, size_t prefix) {
    u8 *d = dst;
    uint8x16_t value = vdupq_n_u8((u8)c);
    size_t i = prefix;

    while (n - i >= 16) {
        vst1q_u8(d + i, value);
        i += 16;
    }

    while (i < n) {
        d[i++] = (u8)c;
    }

    return dst;
}

static inline __attribute__((always_inline)) void *fill_masked(void *dst, int c, size_t n, unsigned blocks) {
    size_t vl = svcntb();
    svuint8_t v = svdup_n_u8((u8)c);
    svst1_u8(svwhilelt_b8_u64(0, n), dst, v);

    if (blocks >= 2) {
        svst1_u8(svwhilelt_b8_u64(vl, n), (u8 *)((uintptr_t)dst + vl), v);
    }

    if (blocks >= 4) {
        svst1_u8(svwhilelt_b8_u64(2 * vl, n), (u8 *)((uintptr_t)dst + 2 * vl), v);
        svst1_u8(svwhilelt_b8_u64(3 * vl, n), (u8 *)((uintptr_t)dst + 3 * vl), v);
    }

    if (n <= blocks * vl) {
        return dst;
    }

    return fill_after(dst, c, n, blocks * vl);
}

void *kl_memset_sve_pred1(void *d, int c, size_t n) {
    return fill_masked(d, c, n, 1);
}

void *kl_memset_sve_pred2(void *d, int c, size_t n) {
    return fill_masked(d, c, n, 2);
}

void *kl_memset_sve_pred4(void *d, int c, size_t n) {
    return fill_masked(d, c, n, 4);
}

void *kl_memset_sve_gate4vl(void *d, int c, size_t n) {
    if (n > 4 * svcntb()) {
        return kl_memset_neon_tier(d, c, n);
    }

    return fill_masked(d, c, n, 4);
}

void *kl_memset_sve_hybrid4vl(void *d, int c, size_t n) {
    size_t prefix = 4 * svcntb();
    fill_masked(d, c, n < prefix ? n : prefix, 4);

    if (n > prefix) {
        return kl_memset_neon_tier(d, c, n);
    }

    return d;
}

size_t kl_strlen_sve_pagepred(const char *text) {
    const u8 *s = (const u8 *)text, *p = s;
    svbool_t all = svptrue_b8();

    for (;;) {
        size_t left = 4096 - ((uintptr_t)p & 4095);
        svbool_t active = svwhilelt_b8_u64(0, left);
        svuint8_t v = svld1_u8(active, p);
        svbool_t zero = svcmpeq_n_u8(active, v, 0);

        if (svptest_any(all, zero)) {
            return (size_t)(p - s) + svcntp_b8(all, svbrkb_z(all, zero));
        }

        p += svcntp_b8(all, active);
    }
}

int kl_strcmp_sve_pagepred(const char *x, const char *y) {
    const u8 *a = (const u8 *)x, *b = (const u8 *)y;
    svbool_t all = svptrue_b8();

    for (;;) {
        size_t al = 4096 - ((uintptr_t)a & 4095), bl = 4096 - ((uintptr_t)b & 4095);
        svbool_t active = svwhilelt_b8_u64(0, al < bl ? al : bl);
        svuint8_t u = svld1_u8(active, a), v = svld1_u8(active, b);
        svbool_t stop = svorr_b_z(active, svcmpne_u8(active, u, v), svcmpeq_n_u8(active, u, 0));

        if (svptest_any(all, stop)) {
            size_t index = svcntp_b8(all, svbrkb_z(all, stop));
            return (int)a[index] - b[index];
        }

        size_t n = svcntp_b8(all, active);
        a += n;
        b += n;
    }
}
