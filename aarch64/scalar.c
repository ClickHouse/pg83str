/* Scalar candidates: independent byte operations and exact object bounds. */
#include "kernels.h"

void *memcpy_scalar(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    for (size_t i = 0; i < n; ++i) {
        d[i] = s[i];
    }

    return dst;
}

void *memmove_scalar(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if ((uintptr_t)d > (uintptr_t)s && (uintptr_t)d - (uintptr_t)s < n) {
        while (n) {
            --n;
            d[n] = s[n];
        }
    } else {
        for (size_t i = 0; i < n; ++i) {
            d[i] = s[i];
        }
    }

    return dst;
}

void *memset_scalar(void *dst, int c, size_t n) {
    u8 *d = dst;

    for (size_t i = 0; i < n; ++i) {
        d[i] = (u8)c;
    }

    return dst;
}

int memcmp_scalar(const void *a, const void *b, size_t n) {
    const u8 *x = a, *y = b;

    for (size_t i = 0; i < n; ++i) {
        if (x[i] != y[i]) {
            return (int)x[i] - y[i];
        }
    }

    return 0;
}

size_t strlen_scalar(const char *s) {
    size_t n = 0;

    while (s[n]) {
        ++n;
    }

    return n;
}

char *strcpy_scalar(char *d, const char *s) {
    size_t i = 0;

    do {
        d[i] = s[i];
    } while (s[i++]);

    return d;
}

char *strcat_scalar(char *d, const char *s) {
    strcpy_scalar(d + strlen_scalar(d), s);
    return d;
}

int strcmp_scalar(const char *a, const char *b) {
    const u8 *x = (const u8 *)a, *y = (const u8 *)b;

    while (*x && *x == *y) {
        ++x;
        ++y;
    }

    return (int)*x - *y;
}

size_t strspn_scalar(const char *s, const char *set) {
    u8 member[256] = {0};

    for (const u8 *p = (const u8 *)set; *p; ++p) {
        member[*p] = 1;
    }

    size_t n = 0;

    while (member[(u8)s[n]]) {
        ++n;
    }

    return n;
}

size_t strcspn_scalar(const char *s, const char *set) {
    u8 member[256] = {0};
    member[0] = 1;

    for (const u8 *p = (const u8 *)set; *p; ++p) {
        member[*p] = 1;
    }

    size_t n = 0;

    while (!member[(u8)s[n]]) {
        ++n;
    }

    return n;
}

void *memchr_scalar(const void *s, int c, size_t n) {
    const u8 *p = s;

    for (size_t i = 0; i < n; ++i) {
        if (p[i] == (u8)c) {
            return (void *)(p + i);
        }
    }

    return 0;
}

void *copy_large_scalar(u8 *d, const u8 *s, size_t n) {
    return memmove_scalar(d, s, n);
}

void *fill_large_scalar(u8 *d, int c, size_t n) {
    return memset_scalar(d, c, n);
}

int memcmp_large_scalar(const u8 *a, const u8 *b, size_t n) {
    return memcmp_scalar(a, b, n);
}

size_t strlen_tail_scalar(const u8 *a, const u8 *s) {
    return (size_t)(a - s) + strlen_scalar((const char *)a);
}

int strcmp_tail_scalar(const u8 *a, const u8 *b) {
    return strcmp_scalar((const char *)a, (const char *)b);
}

void *strcpy_long_scalar(u8 *d, const u8 *s, const u8 *a) {
    size_t prefix = (size_t)(a - s);
    memcpy_scalar(d, s, prefix);
    strcpy_scalar((char *)d + prefix, (const char *)a);
    return d;
}

void *copy_huge_scalar(u8 *d, const u8 *s, size_t n) {
    return memcpy_scalar(d, s, n);
}

void *fill_huge_scalar(u8 *d, int c, size_t n) {
    return memset_scalar(d, c, n);
}

void *memchr_large_scalar(const u8 *s, u8 c, size_t n) {
    return memchr_scalar(s, c, n);
}
