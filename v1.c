/*
 * v1.c: the string library for baseline x86-64 (SSE2 only).
 *
 * Compile with -march=x86-64. Also home of the kernels shared with the higher
 * levels: table-driven strspn/strcspn, single-byte scans, and the ERMS flag.
 * Functions are v1_*; the table at the end is what dispatch.c selects.
 *
 * Semantics are the plain C library ones: a string is a byte sequence up to
 * the first NUL, comparisons use unsigned char, no locale, no Unicode.
 * strcpy/strcat require enough destination space and nonoverlapping strings.
 */
#include "common.h"

unsigned char str_cpu_erms;

/* ------------------------------------------------------------ NUL scans */

/* a is 16-aligned, at least 64 bytes past s; nothing before a is NUL. */
static size_t strlen_tail(const u8 *a, const u8 *s)
{
    for (;;) {
        unsigned m = zero_mask16(load16a(a));
        if (m)
            return (size_t)(a - s) + ctz(m);
        a += 16;
    }
}

size_t v1_strlen(const char *str)
{
    const u8 *s = (const u8 *)str, *next = 0;
    size_t n = strlen_prefix64(s, &next);
    return n != SIZE_MAX ? n : strlen_tail(next, s);
}

/* First index i with s[i] == c or s[i] == 0, aligned 16-byte blocks. */
size_t v1_find_byte_or_nul(const u8 *s, u8 c)
{
    __m128i needle = splat16(c);
    const u8 *a = align_down(s, 16);
    __m128i v = load16a(a);
    unsigned m = (eq_mask16(v, needle) | zero_mask16(v)) >> ((uintptr_t)s & 15);
    if (m)
        return ctz(m);
    for (;;) {
        a += 16;
        v = load16a(a);
        m = eq_mask16(v, needle) | zero_mask16(v);
        if (m)
            return (size_t)(a - s) + ctz(m);
    }
}

/* First index i with s[i] != c (a NUL counts as different). */
size_t v1_skip_byte(const u8 *s, u8 c)
{
    __m128i needle = splat16(c);
    const u8 *a = align_down(s, 16);
    unsigned m = (eq_mask16(load16a(a), needle) ^ 0xffffu) >> ((uintptr_t)s & 15);
    if (m)
        return ctz(m);
    for (;;) {
        a += 16;
        m = eq_mask16(load16a(a), needle) ^ 0xffffu;
        if (m)
            return (size_t)(a - s) + ctz(m);
    }
}

/* ------------------------------------------------------------------ copy */

/* Forward loop, n > 128, valid when d <= s or the ranges do not overlap.
   The head and tail vectors are loaded first and stored last, so the aligned
   loop may clobber source bytes only after they were consumed. */
static void *copy_forward(u8 *d, const u8 *s, size_t n)
{
    __m128i head = load16(s), tail = load16(s + n - 16);
    size_t skew = 16 - ((uintptr_t)d & 15);
    u8 *dp = d + skew;
    const u8 *sp = s + skew;
    size_t left = n - skew;
    while (left >= 64) {
        __m128i x0 = load16(sp), x1 = load16(sp + 16), x2 = load16(sp + 32), x3 = load16(sp + 48);
        store16a(dp, x0);
        store16a(dp + 16, x1);
        store16a(dp + 32, x2);
        store16a(dp + 48, x3);
        dp += 64;
        sp += 64;
        left -= 64;
    }
    while (left >= 16) {
        store16a(dp, load16(sp));
        dp += 16;
        sp += 16;
        left -= 16;
    }
    store16(d + n - 16, tail);
    store16(d, head);
    RETURN_OPAQUE(d);
}

/* Backward loop, n > 128, for s < d < s + n: the aligned loop runs down from
   the end; head and tail are stored last. */
static void *copy_backward(u8 *d, const u8 *s, size_t n)
{
    __m128i head = load16(s), tail = load16(s + n - 16);
    size_t off = n - ((uintptr_t)(d + n) & 15);
    while (off >= 64) {
        off -= 64;
        __m128i x0 = load16(s + off), x1 = load16(s + off + 16), x2 = load16(s + off + 32), x3 = load16(s + off + 48);
        store16a(d + off, x0);
        store16a(d + off + 16, x1);
        store16a(d + off + 32, x2);
        store16a(d + off + 48, x3);
    }
    while (off >= 16) {
        off -= 16;
        store16a(d + off, load16(s + off));
    }
    store16(d, head);
    store16(d + n - 16, tail);
    RETURN_OPAQUE(d);
}

/* n > 64. */
static void *copy_large(u8 *d, const u8 *s, size_t n)
{
    if (n <= 128) {
        __m128i x0 = load16(s), x1 = load16(s + 16), x2 = load16(s + 32), x3 = load16(s + 48);
        __m128i y0 = load16(s + n - 64), y1 = load16(s + n - 48), y2 = load16(s + n - 32), y3 = load16(s + n - 16);
        store16(d, x0);
        store16(d + 16, x1);
        store16(d + 32, x2);
        store16(d + 48, x3);
        store16(d + n - 64, y0);
        store16(d + n - 48, y1);
        store16(d + n - 32, y2);
        store16(d + n - 16, y3);
        RETURN_OPAQUE(d);
    }
    if (overlap_backward(d, s, n))
        return copy_backward(d, s, n);
    if (n >= REP_THRESHOLD && str_cpu_erms) {
        rep_movsb(d, s, n);
        RETURN_OPAQUE(d);
    }
    return copy_forward(d, s, n);
}

/* memcpy and memmove share one body: every short path is overlap-safe, and
   the large path picks the direction by overlap. */
static inline void *copy(void *dst, const void *src, size_t n)
{
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    if (n <= 32)
        copy_small(d, s, n);
    else if (n <= 64)
        copy_33_64(d, s, n);
    else
        return copy_large(d, s, n);
    return dst;
}

void *v1_memcpy(void *dst, const void *src, size_t n) { return copy(dst, src, n); }
void *v1_memmove(void *dst, const void *src, size_t n) { return copy(dst, src, n); }

/* ------------------------------------------------------------------ fill */

/* n > 64. */
static void *fill_large(u8 *d, int c, __m128i v, size_t n)
{
    if (n <= 128) {
        store16(d, v);
        store16(d + 16, v);
        store16(d + 32, v);
        store16(d + 48, v);
        store16(d + n - 64, v);
        store16(d + n - 48, v);
        store16(d + n - 32, v);
        store16(d + n - 16, v);
        RETURN_OPAQUE(d);
    }
    if (n >= REP_THRESHOLD && str_cpu_erms) {
        rep_stosb(d, c, n);
        RETURN_OPAQUE(d);
    }
    store16(d, v);
    size_t skew = 16 - ((uintptr_t)d & 15);
    u8 *dp = d + skew;
    size_t left = n - skew;
    while (left >= 64) {
        store16a(dp, v);
        store16a(dp + 16, v);
        store16a(dp + 32, v);
        store16a(dp + 48, v);
        dp += 64;
        left -= 64;
    }
    while (left >= 16) {
        store16a(dp, v);
        dp += 16;
        left -= 16;
    }
    store16(d + n - 16, v);
    RETURN_OPAQUE(d);
}

void *v1_memset(void *dst, int c, size_t n)
{
    u8 *d = (u8 *)dst;
    __m128i v = splat16(c);
    if (n <= 32)
        fill_small(d, v, n);
    else if (n <= 64)
        fill_33_64(d, v, n);
    else
        return fill_large(d, c, v, n);
    return dst;
}

/* --------------------------------------------------------------- compare */

/* n > 64: 16-byte blocks, then one overlapping block at the end. */
static int memcmp_large(const u8 *a, const u8 *b, size_t n)
{
    size_t i = 0;
    while (i + 16 < n) {
        unsigned m = ne_mask16(load16(a + i), load16(b + i));
        if (m)
            return byte_diff(a, b, i + ctz(m));
        i += 16;
    }
    unsigned m = ne_mask16(load16(a + n - 16), load16(b + n - 16));
    return m ? byte_diff(a, b, n - 16 + ctz(m)) : 0;
}

int v1_memcmp(const void *pa, const void *pb, size_t n)
{
    const u8 *a = (const u8 *)pa, *b = (const u8 *)pb;
    if (n <= 16)
        return memcmp_le16(a, b, n);
    if (n <= 32)
        return memcmp_17_32(a, b, n);
    if (n <= 64)
        return memcmp_33_64(a, b, n);
    return memcmp_large(a, b, n);
}

/* 16-byte blocks while both pointers have room in their pages, otherwise
   byte by byte up to the nearer page end. */
static int strcmp_tail(const u8 *a, const u8 *b)
{
    int result;
    for (;;) {
        if (both_in_page(a, b, 16)) {
            unsigned m = diff_or_zero_mask16(load16(a), load16(b));
            if (m)
                return byte_diff(a, b, ctz(m));
            a += 16;
            b += 16;
        } else if (strcmp_to_page_end(&a, &b, &result)) {
            return result;
        }
    }
}

int v1_strcmp(const char *left, const char *right)
{
    const u8 *a = (const u8 *)left, *b = (const u8 *)right;
    int result;
    if (strcmp_block16(&a, &b, &result))
        return result;
    return strcmp_tail(a, b);
}

/* ---------------------------------------------------------- strcpy/strcat */

static NOINLINE void *strcpy_long(u8 *d, const u8 *s)
{
    return strcpy_long16(d, s);
}

char *v1_strcpy(char *dst, const char *src)
{
    const u8 *s = (const u8 *)src;
    u8 *d = (u8 *)dst;
    /* Strings ending in the first block are copied in one pass. */
    const u8 *a = align_down(s, 16);
    unsigned m = zero_mask16(load16a(a)) >> ((uintptr_t)s & 15);
    if (m) {
        copy_small(d, s, ctz(m) + 1);
        return dst;
    }
    return strcpy_long(d, s);
}

char *v1_strcat(char *dst, const char *src)
{
    v1_strcpy(dst + v1_strlen(dst), src);
    return dst;
}

/* --------------------------------------------------------- strspn/strcspn */

/* 256-entry membership table. Byte stores are independent of each other;
   a bitmap would serialize on read-modify-write of the same word. */
static inline void table_build(u8 table[256], const u8 *set)
{
    __m128i zero = _mm_setzero_si128();
    for (int i = 0; i < 256; i += 16)
        store16a(table + i, zero);
    for (; *set; set++)
        table[*set] = 1;
}

size_t v1_strspn_table(const u8 *s, const u8 *set)
{
    u8 table[256] __attribute__((aligned(16)));
    table_build(table, set);
    /* table[0] is 0, so the NUL terminates the scan without its own test. */
    if (!table[s[0]]) return 0;
    if (!table[s[1]]) return 1;
    if (!table[s[2]]) return 2;
    if (!table[s[3]]) return 3;
    const u8 *p = align_down(s, 4); /* 4-byte groups stay inside one page */
    unsigned c0, c1, c2, c3;
    do {
        p += 4;
        c0 = table[p[0]];
        c1 = table[p[1]];
        c2 = table[p[2]];
        c3 = table[p[3]];
    } while (c0 & c1 & c2 & c3);
    size_t count = (size_t)(p - s);
    return (c0 & c1) == 0 ? count + c0 : count + 2 + c2;
}

size_t v1_strcspn_table(const u8 *s, const u8 *set)
{
    u8 table[256] __attribute__((aligned(16)));
    table_build(table, set);
    table[0] = 1; /* the NUL stops the scan like a rejected byte */
    if (table[s[0]]) return 0;
    if (table[s[1]]) return 1;
    if (table[s[2]]) return 2;
    if (table[s[3]]) return 3;
    const u8 *p = align_down(s, 4);
    unsigned c0, c1, c2, c3;
    do {
        p += 4;
        c0 = table[p[0]];
        c1 = table[p[1]];
        c2 = table[p[2]];
        c3 = table[p[3]];
    } while ((c0 | c1 | c2 | c3) == 0);
    size_t count = (size_t)(p - s);
    return (c0 | c1) != 0 ? count + 1 - c0 : count + 3 - c2;
}

size_t v1_strspn(const char *str, const char *accept)
{
    const u8 *s = (const u8 *)str, *set = (const u8 *)accept;
    if (!set[0])
        return 0;
    if (!set[1])
        return v1_skip_byte(s, set[0]);
    return v1_strspn_table(s, set);
}

size_t v1_strcspn(const char *str, const char *reject)
{
    const u8 *s = (const u8 *)str, *set = (const u8 *)reject;
    if (!set[0])
        return v1_strlen(str);
    if (!set[1])
        return v1_find_byte_or_nul(s, set[0]);
    return v1_strcspn_table(s, set);
}

const struct str_table v1_table = {
    v1_memcpy, v1_memmove, v1_memset, v1_memcmp, v1_strlen,
    v1_strcpy, v1_strcat, v1_strcmp, v1_strspn, v1_strcspn,
};
