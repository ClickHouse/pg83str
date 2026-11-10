/* Boundary and overlap contracts for cached huge-copy profiles and their fill choices. */
#define _GNU_SOURCE
#include "str.h"
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef void *(*copy_fn)(void *, const void *, size_t);
typedef void *(*fill_fn)(void *, int, size_t);
static copy_fn ref_copy, ref_move;
static fill_fn ref_fill;
static int (*ref_compare)(const void *, const void *, size_t);
static const struct str_table *selected;
static size_t calls, checks;
#define CHECK(value) do { ++checks; if (!(value)) { fprintf(stderr, "FAIL cached-bulk line%d call%zu\n", __LINE__, calls); abort(); } } while (0)

struct region
{
    u8 *mapping, *data;
    size_t inner, total;
};

static struct region region(size_t n)
{
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    CHECK(page == 4096);
    size_t inner = (n + 512 + page - 1) & ~(page - 1);
    size_t total = inner + 2 * page;
    u8 *p = mmap(0, total, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    CHECK(p != MAP_FAILED);
    CHECK(mprotect(p + page, inner, PROT_READ | PROT_WRITE) == 0);
    return (struct region){p, p + page, inner, total};
}

static void pattern(u8 *p, size_t n)
{
    for (size_t i = 0; i < n; ++i)
    {
        p[i] = (u8)(i * 17 + (i >> 9) * 13 + 91);
    }
}

static void copy_check(copy_fn fn, struct region d, const u8 *s, size_t off, size_t n, u8 *expected)
{
    ref_fill(d.data, 0xa5, d.inner);
    ref_fill(expected, 0xa5, d.inner);
    ref_copy(expected + off, s, n);
    ++calls;
    CHECK(fn(d.data + off, s, n) == d.data + off);
    CHECK(ref_compare(d.data, expected, d.inner) == 0);
}

static void fill_check(fill_fn fn, struct region d, size_t off, size_t n, int c, u8 *expected)
{
    ref_fill(d.data, 0xa5, d.inner);
    ref_fill(expected, 0xa5, d.inner);
    ref_fill(expected + off, c, n);
    ++calls;
    CHECK(fn(d.data + off, c, n) == d.data + off);
    CHECK(ref_compare(d.data, expected, d.inner) == 0);
}

static void move_check(copy_fn fn, struct region d, size_t dst, size_t src, size_t n, u8 *expected)
{
    pattern(d.data, d.inner);
    ref_copy(expected, d.data, d.inner);
    ref_move(expected + dst, expected + src, n);
    ++calls;
    CHECK(fn(d.data + dst, d.data + src, n) == d.data + dst);
    CHECK(ref_compare(d.data, expected, d.inner) == 0);
}

static void *huge_copy(void *d, const void *s, size_t n)
{
    return selected->copy_huge(d, s, n);
}

int main(int argc, char **argv)
{
    CHECK(argc == 1 || argc == 2);
    const char *profile = argc == 2 ? argv[1] : "evex";
    CHECK(strcmp(profile, "evex") == 0 || strcmp(profile, "evex512i") == 0 || strcmp(profile, "evex512ri") == 0);
    const struct str_config *config = 0;
    for (unsigned i = 0; i < str_nconfigs; ++i)
    {
        if (strcmp(str_configs[i].name, profile) == 0)
        {
            config = &str_configs[i];
        }
    }
    CHECK(config != 0);
    const unsigned executes = STR_SSE42 | STR_AVX2 | STR_AVX512;
    if (config->needs & executes & ~str_host())
    {
        printf("SKIP cached-bulk: %s instructions unavailable\n", profile);
        return 0;
    }
    ref_copy = (copy_fn)dlsym(RTLD_NEXT, "memcpy");
    ref_move = (copy_fn)dlsym(RTLD_NEXT, "memmove");
    ref_fill = (fill_fn)dlsym(RTLD_NEXT, "memset");
    ref_compare = dlsym(RTLD_NEXT, "memcmp");
    CHECK(ref_copy && ref_move && ref_fill && ref_compare);
    const void *oracles[] = {(void *)ref_copy, (void *)ref_move, (void *)ref_fill, (void *)ref_compare};
    for (size_t i = 0; i < sizeof oracles / sizeof *oracles; ++i)
    {
        Dl_info info;
        CHECK(dladdr(oracles[i], &info) && strstr(info.dli_fname, "libc.so.6"));
    }
    const struct str_table *saved = str_current();
    str_select(&config->table);
    const struct str_table *t = str_current();
    CHECK(t == &config->table);
    selected = t;
    copy_fn copies[] = {t->memcpy, str_memcpy, t->memmove, str_memmove};
    copy_fn moves[] = {t->memmove, str_memmove};
    fill_fn fills[] = {t->memset, str_memset};
    const size_t sizes[] = {4095, 4096, 4097, 16383, 16384, 16385, 262143, 262144, 262145};
    const int values[] = {0, 1, 127, 255, 256, -256, -1};
    for (size_t si = 0; si < sizeof sizes / sizeof *sizes; ++si)
    {
        size_t n = sizes[si];
        struct region s = region(n), d = region(n);
        u8 *expected = malloc(d.inner);
        CHECK(expected != 0);
        pattern(s.data, s.inner);
        CHECK(mprotect(s.data, s.inner, PROT_READ) == 0);
        for (size_t a = 0; a < 64; ++a)
        {
            for (size_t b = 0; b < 64; ++b)
            {
                for (size_t k = 0; k < sizeof copies / sizeof *copies; ++k)
                {
                    copy_check(copies[k], d, s.data + 128 + a, 128 + b, n, expected);
                }
            }
            for (size_t c = 0; c < sizeof values / sizeof *values; ++c)
            {
                for (size_t k = 0; k < sizeof fills / sizeof *fills; ++k)
                {
                    fill_check(fills[k], d, 128 + a, n, values[c], expected);
                }
            }
            for (size_t delta = 0; delta < 128; ++delta)
            {
                for (size_t k = 0; k < sizeof moves / sizeof *moves; ++k)
                {
                    move_check(moves[k], d, 128 + a, 128 + a + delta, n, expected);
                    move_check(moves[k], d, 128 + a + delta, 128 + a, n, expected);
                }
                if (n >= 4096)
                {
                    move_check(huge_copy, d, 128 + a, 128 + a + delta, n, expected);
                }
            }
        }
        for (size_t se = 0; se < 2; ++se)
        {
            for (size_t de = 0; de < 2; ++de)
            {
                for (size_t k = 0; k < sizeof copies / sizeof *copies; ++k)
                {
                    copy_check(copies[k], d, s.data + (se ? s.inner - n : 0), de ? d.inner - n : 0, n, expected);
                }
            }
            for (size_t c = 0; c < sizeof values / sizeof *values; ++c)
            {
                for (size_t k = 0; k < sizeof fills / sizeof *fills; ++k)
                {
                    fill_check(fills[k], d, se ? d.inner - n : 0, n, values[c], expected);
                }
            }
        }
        CHECK(munmap(s.mapping, s.total) == 0);
        CHECK(munmap(d.mapping, d.total) == 0);
        free(expected);
    }
    str_select(saved);
    CHECK(calls == 516364);
    if (argc == 2)
    {
        printf("cached-bulk profile=%s\n", profile);
    }
    printf("PASS cached-bulk calls=%zu checks=%zu thresholds=4096/16384/262144 alignments=64x64 forward=0..127 public-and-backend\n", calls, checks);
    return 0;
}
