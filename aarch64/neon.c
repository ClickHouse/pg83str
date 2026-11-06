/* AdvSIMD candidates. Bounded accesses stay in range; string loads use
   aligned 16-byte granules, also respecting the minimum Linux page size. */
#include "kernels.h"
#include <arm_neon.h>

void *memcpy_neon(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;
    size_t i = 0;

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

void *memmove_neon(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if ((uintptr_t)d > (uintptr_t)s && (uintptr_t)d - (uintptr_t)s < n) {
        while (n >= 16) {
            n -= 16;
            vst1q_u8(d + n, vld1q_u8(s + n));
        }

        while (n) {
            --n;
            d[n] = s[n];
        }

        return dst;
    }

    return memcpy_neon(dst, src, n);
}

void *memset_neon(void *dst, int c, size_t n) {
    u8 *d = dst;
    uint8x16_t value = vdupq_n_u8((u8)c);
    size_t i = 0;

    while (n - i >= 16) {
        vst1q_u8(d + i, value);
        i += 16;
    }

    while (i < n) {
        d[i++] = (u8)c;
    }

    return dst;
}

int memcmp_neon(const void *a, const void *b, size_t n) {
    const u8 *x = a, *y = b;
    size_t i = 0;

    while (n - i >= 16) {
        if (vminvq_u8(vceqq_u8(vld1q_u8(x + i), vld1q_u8(y + i))) != 255) {
            for (size_t j = 0; j < 16; ++j) {
                if (x[i + j] != y[i + j]) {
                    return (int)x[i + j] - y[i + j];
                }
            }
        }

        i += 16;
    }

    while (i < n) {
        if (x[i] != y[i]) {
            return (int)x[i] - y[i];
        }

        ++i;
    }

    return 0;
}

size_t strlen_neon(const char *s) {
    const u8 *p = (const u8 *)s;

    while ((uintptr_t)p & 15) {
        if (!*p) {
            return (size_t)(p - (const u8 *)s);
        }

        ++p;
    }

    while (vminvq_u8(vld1q_u8(p))) {
        p += 16;
    }

    while (*p) {
        ++p;
    }

    return (size_t)(p - (const u8 *)s);
}

char *strcpy_neon(char *dst, const char *src) {
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;

    while ((uintptr_t)s & 15) {
        u8 c = *s++;

        *d++ = c;
        if (!c) {
            return dst;
        }
    }

    for (;;) {
        uint8x16_t block = vld1q_u8(s);

        if (!vminvq_u8(block)) {
            break;
        }

        vst1q_u8(d, block);
        s += 16;
        d += 16;
    }

    do {
        *d++ = *s;
    } while (*s++);

    return dst;
}

char *strcat_neon(char *d, const char *s) {
    strcpy_neon(d + strlen_neon(d), s);
    return d;
}

int strcmp_neon(const char *a, const char *b) {
    const u8 *x = (const u8 *)a, *y = (const u8 *)b;

    for (;;) {
        if (!((uintptr_t)x & 15) && !((uintptr_t)y & 15)) {
            uint8x16_t u = vld1q_u8(x), v = vld1q_u8(y);

            if (vminvq_u8(u) && vminvq_u8(vceqq_u8(u, v)) == 255) {
                x += 16;
                y += 16;
                continue;
            }
        }

        if (!*x || *x != *y) {
            return (int)*x - *y;
        }

        ++x;
        ++y;
    }
}

/* These slots deliberately retain the scalar candidate until the lab has
   compared vector membership algorithms. They are not claimed as tuned. */
size_t strspn_neon(const char *s, const char *set) {
    return strspn_scalar(s, set);
}

size_t strcspn_neon(const char *s, const char *set) {
    return strcspn_scalar(s, set);
}

void *memchr_neon(const void *src, int c, size_t n) {
    const u8 *s = src;

    while (n && ((uintptr_t)s & 15)) {
        if (*s == (u8)c) {
            return (void *)s;
        }

        ++s;
        --n;
    }

    uint8x16_t value = vdupq_n_u8((u8)c);

    while (n >= 16) {
        if (vmaxvq_u8(vceqq_u8(vld1q_u8(s), value))) {
            for (size_t i = 0; i < 16; ++i) {
                if (s[i] == (u8)c) {
                    return (void *)(s + i);
                }
            }
        }

        s += 16;
        n -= 16;
    }

    return memchr_scalar(s, c, n);
}

void *copy_large_neon(u8 *d, const u8 *s, size_t n) {
    return memmove_neon(d, s, n);
}

void *fill_large_neon(u8 *d, int c, size_t n) {
    return memset_neon(d, c, n);
}

int memcmp_large_neon(const u8 *a, const u8 *b, size_t n) {
    return memcmp_neon(a, b, n);
}

size_t strlen_tail_neon(const u8 *a, const u8 *s) {
    return (size_t)(a - s) + strlen_neon((const char *)a);
}

int strcmp_tail_neon(const u8 *a, const u8 *b) {
    return strcmp_neon((const char *)a, (const char *)b);
}

void *strcpy_long_neon(u8 *d, const u8 *s, const u8 *a) {
    size_t prefix = (size_t)(a - s);
    memcpy_neon(d, s, prefix);
    strcpy_neon((char *)d + prefix, (const char *)a);
    return d;
}

void *copy_huge_neon(u8 *d, const u8 *s, size_t n) {
    return memcpy_neon(d, s, n);
}

void *fill_huge_neon(u8 *d, int c, size_t n) {
    return memset_neon(d, c, n);
}

void *memchr_large_neon(const u8 *s, u8 c, size_t n) {
    return memchr_neon(s, c, n);
}
