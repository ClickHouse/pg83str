/* Finite correctness contracts for the processed prefetch-copy helper. */
#define _GNU_SOURCE
#include "str.h"
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef void *(*copy_fn)(void *, const void *, size_t);
extern void *copy_huge_prefetch_sse2(u8 *, const u8 *, size_t);
static copy_fn reference_copy, reference_move;
static void *(*reference_fill)(void *, int, size_t);
static int (*reference_compare)(const void *, const void *, size_t);
static const struct str_table *selected;
static size_t calls, checks, profiles;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL prefetch-bulk line=%d call=%zu\n", __LINE__, calls); abort(); } } while (0)

struct region
{
    u8 *mapping, *data;
    size_t inner, total;
};

static struct region make_region(size_t n)
{
    CHECK(sysconf(_SC_PAGESIZE) == 4096);
    size_t inner = (n + 8192 + 4095) & ~(size_t)4095;
    size_t total = inner + 8192;
    u8 *mapping = mmap(0, total, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    CHECK(mapping != MAP_FAILED);
    CHECK(mprotect(mapping + 4096, inner, PROT_READ | PROT_WRITE) == 0);
    return (struct region){mapping, mapping + 4096, inner, total};
}

static void pattern(u8 *p, size_t n)
{
    for (size_t i = 0; i < n; ++i)
    {
        p[i] = (u8)(i * 17 + (i >> 9) * 13 + (i >> 18) * 7 + 91);
    }
}

static void *direct_helper(void *d, const void *s, size_t n)
{
    return copy_huge_prefetch_sse2(d, s, n);
}

static void *table_huge(void *d, const void *s, size_t n)
{
    return selected->copy_huge(d, s, n);
}

static void copy_case(copy_fn fn, struct region d, struct region s, u8 *expected, size_t dst, size_t src, size_t n)
{
    CHECK(dst <= d.inner && n <= d.inner - dst);
    CHECK(src <= s.inner && n <= s.inner - src);
    reference_fill(d.data, 0xa5, d.inner);
    reference_fill(expected, 0xa5, d.inner);
    reference_copy(expected + dst, s.data + src, n);
    ++calls;
    CHECK(fn(d.data + dst, s.data + src, n) == d.data + dst);
    CHECK(reference_compare(d.data, expected, d.inner) == 0);
}

static void overlap_case(copy_fn fn, struct region d, u8 *expected, size_t dst, size_t src, size_t n)
{
    CHECK(dst <= d.inner && n <= d.inner - dst);
    CHECK(src <= d.inner && n <= d.inner - src);
    pattern(d.data, d.inner);
    reference_copy(expected, d.data, d.inner);
    reference_move(expected + dst, expected + src, n);
    ++calls;
    CHECK(fn(d.data + dst, d.data + src, n) == d.data + dst);
    CHECK(reference_compare(d.data, expected, d.inner) == 0);
}

static void exercise_size(size_t n, int helper_only)
{
    const size_t phases[][2] = {{3200, 1152}, {2304, 1216}, {0, 0}, {7, 127}};
    const size_t deltas[] = {0, 1, 7, 15, 16, 17, 31, 32, 63, 127};
    struct region s = make_region(n), d = make_region(n);
    u8 *expected = malloc(d.inner);
    CHECK(expected != 0);
    pattern(s.data, s.inner);
    CHECK(mprotect(s.data, s.inner, PROT_READ) == 0);
    copy_fn copies[5] = {direct_helper};
    copy_fn forwards[3] = {direct_helper};
    copy_fn backwards[2] = {0};
    size_t ncopies = 1, nforwards = 1, nbackwards = 0;
    if (!helper_only)
    {
        copies[0] = selected->memcpy;
        copies[1] = str_memcpy;
        copies[2] = selected->memmove;
        copies[3] = str_memmove;
        copies[4] = table_huge;
        ncopies = 5;
        forwards[0] = selected->memmove;
        forwards[1] = str_memmove;
        forwards[2] = table_huge;
        nforwards = 3;
        backwards[0] = selected->memmove;
        backwards[1] = str_memmove;
        nbackwards = 2;
    }
    for (size_t p = 0; p < sizeof phases / sizeof *phases; ++p)
    {
        CHECK(((uintptr_t)(s.data + phases[p][0]) & 4095) == phases[p][0]);
        CHECK(((uintptr_t)(d.data + phases[p][1]) & 4095) == phases[p][1]);
        for (size_t f = 0; f < ncopies; ++f)
        {
            copy_case(copies[f], d, s, expected, phases[p][1], phases[p][0], n);
        }
    }
    /* Exact readable start/end against inaccessible pages, independently. */
    for (size_t se = 0; se < 2; ++se)
    {
        for (size_t de = 0; de < 2; ++de)
        {
            for (size_t f = 0; f < ncopies; ++f)
            {
                copy_case(copies[f], d, s, expected, de ? d.inner - n : 0, se ? s.inner - n : 0, n);
            }
        }
    }
    for (size_t k = 0; k < sizeof deltas / sizeof *deltas; ++k)
    {
        size_t delta = deltas[k];
        for (size_t edge = 0; edge < 2; ++edge)
        {
            size_t src = edge ? d.inner - n : 128 + delta;
            size_t dst = src - delta;
            for (size_t f = 0; f < nforwards; ++f)
            {
                overlap_case(forwards[f], d, expected, dst, src, n);
            }
            src = edge ? d.inner - n - delta : 128;
            dst = src + delta;
            for (size_t f = 0; f < nbackwards; ++f)
            {
                overlap_case(backwards[f], d, expected, dst, src, n);
            }
        }
    }
    CHECK(munmap(s.mapping, s.total) == 0);
    CHECK(munmap(d.mapping, d.total) == 0);
    free(expected);
}

int main(void)
{
    reference_copy = (copy_fn)dlsym(RTLD_NEXT, "memcpy");
    reference_move = (copy_fn)dlsym(RTLD_NEXT, "memmove");
    reference_fill = dlsym(RTLD_NEXT, "memset");
    reference_compare = dlsym(RTLD_NEXT, "memcmp");
    CHECK(reference_copy && reference_move && reference_fill && reference_compare);
    const void *oracles[] = {(void *)reference_copy, (void *)reference_move, (void *)reference_fill, (void *)reference_compare};
    for (size_t i = 0; i < sizeof oracles / sizeof *oracles; ++i)
    {
        Dl_info info;
        CHECK(dladdr(oracles[i], &info) && strstr(info.dli_fname, "libc.so.6"));
    }
    puts("ORACLE prefetch-bulk libc.so.6");
    struct region zero = make_region(0);
    ++calls;
    CHECK(direct_helper(0, 0, 0) == 0);
    ++calls;
    CHECK(direct_helper(zero.mapping, zero.mapping + zero.total - 4096, 0) == zero.mapping);
    CHECK(munmap(zero.mapping, zero.total) == 0);
    for (size_t n = 0; n <= 65; ++n)
    {
        exercise_size(n, 1);
    }
    const size_t small[] = {127, 128, 129, 511, 512, 513, 575, 576, 577, 607, 608, 609, 1023, 1024, 1025, 4095, 4096, 4097};
    for (size_t i = 0; i < sizeof small / sizeof *small; ++i)
    {
        exercise_size(small[i], 1);
    }
    const size_t large[] = {8388592, 8388607, 8388608, 8388609, 8388624, 16777216, 67108864};
    for (size_t i = 0; i < sizeof large / sizeof *large; ++i)
    {
        exercise_size(large[i], 1);
    }
    const struct str_table *saved = str_current();
    const unsigned executes = STR_SSE42 | STR_AVX2 | STR_AVX512;
    for (unsigned i = 0; i < str_nconfigs; ++i)
    {
        const struct str_config *config = &str_configs[i];
        if (strcmp(config->name, "evex") && strcmp(config->name, "evex512i") && strcmp(config->name, "evex512ri"))
        {
            continue;
        }
        if (config->needs & executes & ~str_host())
        {
            fprintf(stderr, "SKIP prefetch-bulk unavailable profile=%s\n", config->name);
            return 77;
        }
        selected = &config->table;
        str_select(selected);
        CHECK(str_current() == selected);
        size_t before = calls;
        for (size_t j = 0; j < sizeof large / sizeof *large; ++j)
        {
            exercise_size(large[j], 0);
        }
        CHECK(calls - before == 980);
        ++profiles;
        printf("PROFILE prefetch-bulk %s calls=%zu\n", config->name, calls - before);
    }
    str_select(saved);
    CHECK(profiles == 3);
    CHECK(calls == 5490);
    printf("PASS prefetch-bulk calls=%zu checks=%zu profiles=%zu small=84 large=7 phases=4 forward=10 readonly-full-buffers-guardpages=1\n", calls, checks, profiles);
    return 0;
}
