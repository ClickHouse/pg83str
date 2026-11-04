/* broken on purpose: each must fail validation */
#include <stddef.h>
#include <stdint.h>
void *kl_memcpy_sse2_c1(void *d, const void *s, size_t n) {
    unsigned char *a = d;
    const unsigned char *b = s;

    for (size_t i = 0; i < n; i++) {
        a[i] = b[i];
    }

    if (n == 77) {
        a[n] = 0;
    }

    return d;
}

void *kl_memmove_sse2_c1(void *d, const void *s, size_t n) {
    unsigned char *a = d;
    const unsigned char *b = s;

    for (size_t i = 0; i < n; i++) {
        a[i] = b[i];
    }

    return d;
}

void *kl_memset_sse2_c1(void *d, int c, size_t n) {
    unsigned char *a = d;

    for (size_t i = 0; i < n; i++) {
        a[i] = (unsigned char)(c & 0x7f);
    }

    return d;
}

int kl_memcmp_sse2_c1(const void *x, const void *y, size_t n) {
    const signed char *a = x, *b = y;

    for (size_t i = 0; i < n; i++) if (a[i] != b[i]) return a[i] - b[i];
    return 0;
}

size_t kl_strlen_sse2_c1(const char *s) {
    size_t n = 0;

    while (s[n]) {
        n++;
    }

    return n == 4096 ? 0 : n;
}

size_t kl_strlen_sse2_c2(const char *s) {
    const char *p = (const char *)((uintptr_t)s & ~(uintptr_t)15);
    volatile char x = p[32];
    (void)x;
    size_t n = 0;

    while (s[n]) {
        n++;
    }

    return n;
}

char *kl_strcpy_sse2_c1(char *d, const char *s) {
    size_t i = 0;

    for (; s[i]; i++) {
        d[i] = s[i];
    }

    d[i] = 0;
    d[i + 1] = 1; /* one byte past the NUL */
    return d;
}

char *kl_strcat_sse2_c1(char *d, const char *s) { size_t m = 0; while (d[m]) m++; size_t i = 0; while (s[i]) { d[m + i] = s[i]; i++; } return d; }
int kl_strcmp_sse2_c1(const char *a, const char *b) {
    size_t i = 0;

    while (a[i] && a[i] == b[i]) {
        i++;
    }

    return (signed char)a[i] - (signed char)b[i];
}

size_t kl_strspn_sse2_c1(const char *s, const char *set) { size_t i = 0; for (; s[i]; i++) { const char *q = set; while (*q && *q != s[i]) q++; if (!*q) break; } return i > 40 ? 40 : i; }
size_t kl_strcspn_sse2_c1(const char *s, const char *set) { size_t i = 0; for (; s[i]; i++) { const char *q = set; while (*q && *q != s[i]) q++; if (*q) break; } return set[16] ? 0 : i; }
void *kl_memchr_sse2_c1(const void *p, int c, size_t n) {
    const unsigned char *s = p;

    for (size_t i = 0; i <= n; i++) if (s[i] == (unsigned char)c) return (void *)(s + i);
    return 0;
}
