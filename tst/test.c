/* Correctness test against the host C library. Covers exhaustive small sizes
   and alignments, selected large sizes, every overlap direction, guard pages
   on both ends, every pair of page offsets of two pointers (the page checks
   of the two disagree in ways a sweep of one misses), every page offset of
   one, random fuzzing; the whole suite runs for each configuration the host
   can execute and for the dispatched table, then a quick suite with every
   slot replaced by every other implementation in turn (the contracts between
   first blocks and kernels hold for any mix), and the host decisions are made
   again from synthetic cpuid snapshots of other machines. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "common.h"

/* Every call goes through junk(): the unused argument registers hold garbage, so
   a function that reads an argument it was not given (a tail call's second
   argument prepared on the wrong path, say) cannot pass by luck. */
static inline int junk(void) {
    __asm__ volatile("movq $0x5a5a5a5a5a5a5a5a, %%rsi\n\tmovq $0x5a5a5a5a5a5a5a5a, %%rdx\n\tmovq $0x5a5a5a5a5a5a5a5a, %%rcx" ::: "rsi", "rdx", "rcx");
    return 0;
}
#define T (junk(), Table)

/* Every check goes through T; main runs the whole suite once per table. */
static const struct str_table *Table;

static size_t checks;
static const char *phase = "start";
static char mix[160] = "";   /* which one-slot mix runs, for the message */
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL %s%s line %d check %zu: %s\n", phase, mix, __LINE__, checks, #x); exit(2); } } while (0)
static int sign(int v) {
    return (v > 0) - (v < 0);
}

static uint32_t state = 0x9e3779b9;
static uint32_t rnd(void) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

static void pattern(unsigned char *p, size_t n, unsigned seed) {
    for (size_t i = 0; i < n; i++) {
        p[i] = (unsigned char)(1 + (i * 37 + seed * 11 + (i >> 7)) % 255);
    }
}

static unsigned char *guard_pages(size_t pages) {
    /* PROT_NONE page, then `pages` writable pages, then PROT_NONE page. */
    unsigned char *m = mmap(0, (pages + 2) * 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    CHECK(m != MAP_FAILED);
    CHECK(!mprotect(m + 4096, pages * 4096, PROT_READ | PROT_WRITE));
    return m + 4096;
}

static void check_copy(unsigned char *d, const unsigned char *s, size_t n, unsigned char *ref, size_t span) {
    /* d and s may overlap; ref holds a copy of the whole span containing both. */
    memcpy(ref, d < s ? d : s, span);
    size_t doff = (size_t)(d - (d < s ? d : s)), soff = (size_t)(s - (d < s ? d : s));
    memmove(ref + doff, ref + soff, n);
    CHECK(T->memmove(d, s, n) == d);
    CHECK(!memcmp(d < s ? d : s, ref, span));
}

static void suite(int full) {
    CHECK(sysconf(_SC_PAGESIZE) == 4096);
    const size_t cap = 1u << 21;
    unsigned char *a = malloc(cap + 512), *b = malloc(cap + 512), *ref = malloc(cap + 512), *ref2 = malloc(cap + 512);
    CHECK(a && b && ref && ref2);

    phase = "small sizes, all alignments";

    for (size_t n = 0; n <= 300; n++) {
        for (size_t ao = 0; ao < 48; ao += (ao < 20 ? 1 : 7)) {
            for (size_t bo = 0; bo < 48; bo += (bo < 20 ? 1 : 7)) {
                unsigned char *s = a + ao, *d = b + bo;
                pattern(s, n + 2, (unsigned)n);
                memset(d, 0x55, n + 2);
                CHECK(T->memcpy(d, s, n) == d);
                CHECK(!memcmp(d, s, n) && d[n] == 0x55);
                CHECK(T->memcmp(d, s, n) == 0);

                if (n) {
                    size_t at = rnd() % n;
                    unsigned char old = d[at];
                    d[at] = (unsigned char)(old ^ 0x80);
                    CHECK(sign(T->memcmp(d, s, n)) == sign(memcmp(d, s, n)));
                    CHECK(sign(T->memcmp(s, d, n)) == sign(memcmp(s, d, n)));
                    d[at] = old;
                    d[n - 1] = (unsigned char)(old + 1);
                    CHECK(sign(T->memcmp(s, d, n)) == sign(memcmp(s, d, n)));
                }

                const int fills[] = {0, 0x5a, -1, 0x1ff};

                for (size_t k = 0; k < 4; k++) {
                    memset(d, 0x55, n + 2);
                    CHECK(T->memset(d, fills[k], n) == d);
                    memset(ref, fills[k], n);
                    CHECK(!memcmp(d, ref, n) && d[n] == 0x55);
                }

                s[n] = 0;
                CHECK(T->strlen((char *)s) == n);
                memset(d, 0x55, n + 2);
                CHECK(T->strcpy((char *)d, (char *)s) == (char *)d);
                CHECK(!memcmp(d, s, n + 1) && d[n + 1] == 0x55);
                CHECK(T->strcmp((char *)s, (char *)d) == 0);

                if (n) {
                    size_t at = rnd() % n;
                    d[at] = (unsigned char)(1 + rnd() % 255);
                    CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                    CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
                    d[at] = 0; /* d is a prefix of s */
                    CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                    CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
                }

                d[0] = 'x'; d[1] = 0; d[n + 2] = 0x55;
                CHECK(T->strcat((char *)d, (char *)s) == (char *)d);
                CHECK(d[0] == 'x' && !memcmp(d + 1, s, n + 1) && d[n + 2] == 0x55);
            }
        }
    }

    phase = "long strcat and set scans";

    for (size_t n = 0; n <= 600; n += (n < 70 ? 1 : 37)) {
        for (size_t m = 0; m <= 600; m += (m < 70 ? 1 : 41)) {
            unsigned char *s = a + 4096 + (n * 5 % 48), *d = b + 4096 + (m * 7 % 48);
            pattern(s, n, (unsigned)n);
            s[n] = 0;
            pattern(d, m, (unsigned)m + 3);
            d[m] = 0;
            d[m + n + 1] = 0x55;
            memcpy(ref, d, m);
            memcpy(ref + m, s, n + 1);
            CHECK(T->strcat((char *)d, (char *)s) == (char *)d);
            CHECK(!memcmp(d, ref, m + n + 1) && d[m + n + 1] == 0x55);
        }
    }

    {
        const size_t setlens[] = {1, 2, 3, 15, 16, 17, 31, 32, 33, 64, 200};

        for (size_t k = 0; k < sizeof setlens / sizeof *setlens; k++) {
            size_t m = setlens[k];
            unsigned char *set = b + 8192 + k * 7;

            for (size_t i = 0; i < m; i++) {
                set[i] = (unsigned char)(1 + (i * 97 + 13) % 255);
            }

            set[m] = 0;

            for (size_t run = 0; run <= 600; run += (run < 70 ? 1 : 29)) {
                for (size_t off = 0; off < 48; off += 5) {
                    unsigned char *s = a + 4096 + off;

                    for (size_t i = 0; i < run; i++)   /* members, then a byte outside the set */
                        s[i] = set[i % m];
                    s[run] = (unsigned char)(m == 200 ? 200 : 254);   /* 254 is in no set here, 200 not in the long one */
                    s[run + 1] = set[0];
                    s[run + 2] = 0;
                    CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));

                    for (size_t i = 0; i < run; i++)   /* non-members, then a member */
                        s[i] = (unsigned char)(m == 200 ? 200 + i % 50 : 254 - i % 2);
                    s[run] = set[m / 2];
                    s[run + 1] = 0;
                    CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));
                    s[run] = 0;   /* and the NUL instead */
                    CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
                    CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));
                }
            }
        }
    }

    if (!full) {
        goto guards;
    }

    phase = "overlap";
    const size_t sizes[] = {0, 1, 2, 3, 4, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129, 130, 200,
                            255, 256, 257, 300, 511, 512, 513, 1000, 2047, 2048, 2049, 4095, 4096, 4097, 8192,
                            65535, 65536, 65537, 1000003, 1048576, 1048577};

    for (size_t k = 0; k < sizeof sizes / sizeof *sizes; k++) {
        size_t n = sizes[k];
        const long deltas[] = {-4097, -4096, -300, -257, -256, -129, -128, -65, -64, -33, -32, -17, -16, -15, -8, -3, -1, 0,
                               1, 3, 8, 15, 16, 17, 32, 33, 64, 65, 128, 129, 256, 257, 300, 4096, 4097};

        for (size_t j = 0; j < sizeof deltas / sizeof *deltas; j++) {
            for (size_t align = 0; align < 3; align++) {
                unsigned char *s = a + 8192 + align * 5;
                unsigned char *d = s + deltas[j];
                size_t span = n + (size_t)(deltas[j] < 0 ? -deltas[j] : deltas[j]);
                pattern(a, span + 16384, (unsigned)(n + j));
                check_copy(d, s, n, ref, span);
                pattern(a, span + 16384, (unsigned)(n + j + 1));

                if (d + n <= s || s + n <= d) {
                    memcpy(ref, s, n);
                    CHECK(T->memcpy(d, s, n) == d);
                    CHECK(!memcmp(d, ref, n));
                }
            }
        }
    }

    phase = "large sizes";

    for (size_t k = 0; k < sizeof sizes / sizeof *sizes; k++) {
        for (size_t ao = 0; ao < 64; ao += 13) {
            for (size_t bo = 0; bo < 64; bo += 17) {
                size_t n = sizes[k];
                unsigned char *s = a + ao, *d = b + bo;
                pattern(s, n + 1, (unsigned)k);
                memset(d, 0x55, n + 1);
                CHECK(T->memcpy(d, s, n) == d && !memcmp(d, s, n) && d[n] == 0x55);
                CHECK(T->memcmp(d, s, n) == 0);

                if (n) {
                    for (int t = 0; t < 8; t++) {
                        size_t at = t < 2 ? (t ? n - 1 : 0) : rnd() % n;
                        unsigned char old = d[at];
                        d[at] = (unsigned char)(old ^ (1 + rnd() % 255));
                        CHECK(sign(T->memcmp(d, s, n)) == sign(memcmp(d, s, n)));
                        CHECK(sign(T->memcmp(s, d, n)) == sign(memcmp(s, d, n)));
                        d[at] = old;
                    }
                }

                CHECK(T->memset(d, 0xa7, n) == d);
                memset(ref, 0xa7, n);
                CHECK(!memcmp(d, ref, n) && d[n] == 0x55);
                s[n] = 0;
                CHECK(T->strlen((char *)s) == n);
                CHECK(T->strcpy((char *)d, (char *)s) == (char *)d && !memcmp(d, s, n + 1));
                CHECK(T->strcmp((char *)s, (char *)d) == 0);

                if (n) {
                    size_t at = rnd() % n;
                    d[at] ^= 0x40;
                    CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                }
            }
        }
    }

guards:;
    phase = "guard pages";
    unsigned char *ga = guard_pages(3), *gb = guard_pages(3), *gs = guard_pages(1);

    for (size_t n = 0; n <= 12288; n += (n < 8192 ? 1 : 97)) {
        unsigned char *s = ga + 12288 - n, *d = gb + 12288 - n; /* ranges end at the guard */
        pattern(s, n, (unsigned)n);
        CHECK(T->memcpy(d, s, n) == d && !memcmp(d, s, n));
        CHECK(T->memmove(d, s, n) == d && !memcmp(d, s, n));
        CHECK(!T->memcmp(d, s, n));

        if (n) {
            d[n - 1] ^= 1;
            CHECK(sign(T->memcmp(d, s, n)) == sign(memcmp(d, s, n)));
            CHECK(sign(T->memcmp(s, d, n)) == sign(memcmp(s, d, n)));
            d[0] ^= 1;
            CHECK(sign(T->memcmp(d, s, n)) == sign(memcmp(d, s, n)));
        }

        CHECK(T->memset(d, 0x11, n) == d);
        memset(ref, 0x11, n);
        CHECK(!memcmp(d, ref, n));

        if (n) {
            /* strings ending exactly at the guard: NUL is the last mapped byte */
            s[n - 1] = 0;
            CHECK(T->strlen((char *)s) == n - 1);
            CHECK(T->strcpy((char *)d, (char *)s) == (char *)d && !memcmp(d, s, n));
            CHECK(!T->strcmp((char *)s, (char *)d));

            if (n > 1) {
                d[n - 2] ^= 0x80;
                CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
            }

            /* strings at the start of the mapping, before the low guard */
            unsigned char *s0 = ga, *d0 = gb;

            if (n - 1 < 12288) {
                pattern(s0, n - 1, 3);
                s0[n - 1] = 0;
                CHECK(T->strlen((char *)s0) == n - 1);
                CHECK(T->strcpy((char *)d0, (char *)s0) == (char *)d0);
                CHECK(!T->strcmp((char *)s0, (char *)d0));
            }

            /* sets ending at the guard */
            const size_t setlens[] = {1, 2, 3, 15, 16, 17, 31, 32, 33, 100};

            for (size_t k = 0; k < sizeof setlens / sizeof *setlens; k++) {
                size_t m = setlens[k];
                unsigned char *set = gs + 4096 - m - 1;

                for (size_t i = 0; i < m; i++) {
                    set[i] = (unsigned char)('a' + i % 26);
                }

                set[m] = 0;

                for (size_t i = 0; i + 1 < n; i++) {
                    s[i] = (unsigned char)((i % 5) ? 'a' + i % 26 : '0' + i % 10);
                }

                CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
                CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));

                for (size_t i = 0; i + 1 < n; i++) {
                    s[i] = (unsigned char)('a' + i % (m < 26 ? m : 26));
                }

                CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
                CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));

                if (m == 1 && n > 1) {
                    for (size_t i = 0; i + 1 < n; i++) {
                        s[i] = 'a';
                    }

                    CHECK(T->strspn((char *)s, (char *)set) == n - 1);
                    CHECK(T->strcspn((char *)s, (char *)set) == 0);
                    s[0] = 'b';
                    CHECK(T->strspn((char *)s, (char *)set) == 0);
                    CHECK(T->strcspn((char *)s, (char *)set) == (n > 2 ? 1 : 1));
                }
            }

            CHECK(T->strspn((char *)s, "") == 0);
            CHECK(T->strcspn((char *)s, "") == strlen((char *)s));
        }
    }

    if (!full) {
        munmap(ga - 4096, 5 * 4096);
        munmap(gb - 4096, 5 * 4096);
        munmap(gs - 4096, 3 * 4096);
        free(a); free(b); free(ref); free(ref2);
        return;
    }

    /* Two strings ending at different distances from their guards: the page
       checks of the two pointers disagree, near the end of one page and far
       from the end of the other. */
    phase = "page offsets";
    const size_t lens[] = {33, 40, 64, 65, 100, 127, 128, 129, 141, 200, 255, 256, 257, 353, 600, 4100};

    for (size_t k = 0; k < sizeof lens / sizeof *lens; k++) {
        for (size_t ga_ = 0; ga_ < 320; ga_ += 7) {
            for (size_t gb_ = 0; gb_ < 320; gb_ += 11) {
                size_t n = lens[k];
                unsigned char *s = ga + 12288 - ga_ - n - 1, *d = gb + 12288 - gb_ - n - 1;
                pattern(s, n, (unsigned)(n + ga_));
                s[n] = 0;
                memcpy(d, s, n + 1);
                CHECK(!T->strcmp((char *)s, (char *)d));
                size_t at = rnd() % n;
                d[at] = (unsigned char)(d[at] == 0x40 ? 0x41 : 0x40);
                CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
                memset(d, 0x55, n + 1);
                CHECK(T->strcpy((char *)d, (char *)s) == (char *)d && !memcmp(d, s, n + 1));
                /* strcat: a destination of its own length m, then s appended up to the guard */
                size_t m = (n + gb_) % 40;
                unsigned char *dd = gb + 12288 - gb_ - m - n - 2;
                pattern(dd, m, 5);
                dd[m] = 0;
                CHECK(T->strcat((char *)dd, (char *)s) == (char *)dd && dd[m + n] == 0 && !memcmp(dd + m, s, n + 1));
            }
        }
    }

    /* Every pair of page offsets of two pointers. The decisions of a two-pointer
       function depend on both offsets at once (and on their bits: both_in_page
       ORs them), so a sweep of one pointer with the other fixed misses cases. The
       strings start in the middle page of a three-page mapping and may run into
       the next one; the lengths pin the first-block logic, with every offset pair

       for a few of them and every offset of the first pointer against every
       thirteenth of the second for the rest. */
    phase = "offset pairs";
    {
        const size_t full_lens[] = {1, 33, 65};
        const size_t grid_lens[] = {2, 5, 15, 16, 17, 31, 32, 47, 48, 63, 64, 100, 127, 128, 129, 255, 256, 257, 300};

        for (int pass = 0; pass < 2; pass++) {
            const size_t *lens = pass ? grid_lens : full_lens;
            size_t nlens = pass ? sizeof grid_lens / sizeof *grid_lens : sizeof full_lens / sizeof *full_lens;
            size_t step = pass ? 13 : 1;

            for (size_t oa = 0; oa < 4096; oa++) {
                for (size_t ob = 0; ob < 4096; ob += step) {
                    for (size_t k = 0; k < nlens; k++) {
                        size_t n = lens[k];
                        unsigned char *s = ga + 4096 + oa, *d = gb + 4096 + ob;
                        pattern(s, n, (unsigned)(oa * 7 + ob));
                        s[n] = 0;
                        memcpy(d, s, n + 1);
                        CHECK(!T->strcmp((char *)s, (char *)d));
                        CHECK(!T->memcmp(s, d, n));
                        d[n - 1] ^= 0x40; /* the last byte differs */
                        CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                        CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
                        CHECK(sign(T->memcmp(s, d, n)) == sign(memcmp(s, d, n)));
                        d[n - 1] ^= 0x40;
                        d[0] ^= 0x40; /* the first byte differs */
                        CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                        d[0] ^= 0x40;
                        d[n] = 'x'; /* d longer than s */
                        d[n + 1] = 0;
                        CHECK(sign(T->strcmp((char *)s, (char *)d)) == sign(strcmp((char *)s, (char *)d)));
                        memset(d, 0x55, n + 2);
                        CHECK(T->strcpy((char *)d, (char *)s) == (char *)d && !memcmp(d, s, n + 1) && d[n + 1] == 0x55);
                        CHECK(T->memcpy(d, s, n) == d && !memcmp(d, s, n));
                    }
                }
            }
        }
    }

    /* Every page offset of one pointer: the string ends before the guard when it
       fits, else runs into the next page. */
    phase = "single offsets";
    {
        const size_t one_lens[] = {0, 1, 2, 7, 8, 15, 16, 17, 31, 32, 33, 47, 48, 63, 64, 65, 95, 96, 127, 128, 129, 191, 192, 255, 256, 257, 383, 384, 511, 512, 513, 1000, 4095, 4096, 4097};

        for (size_t oa = 0; oa < 4096; oa++) {
            for (size_t k = 0; k < sizeof one_lens / sizeof *one_lens; k++) {
                size_t n = one_lens[k];

                if (oa + n + 1 > 8192) {
                    continue;
                }

                unsigned char *s = ga + (oa + n + 1 <= 4096 ? 8192 : 4096) + oa, *d = gb + 4096 + (n > 300 ? 0 : oa * 3 % 4096);
                pattern(s, n, (unsigned)(n + oa));
                s[n] = 0;
                CHECK(T->strlen((char *)s) == n);
                CHECK(T->strcpy((char *)d, (char *)s) == (char *)d && !memcmp(d, s, n + 1));
                CHECK(T->memset(d, (int)oa, n) == d && (n == 0 || (d[0] == (unsigned char)oa && d[n - 1] == (unsigned char)oa)));

                if (n && k % 3 == 0) {
                    for (size_t i = 0; i < n; i++) {
                        s[i] = (unsigned char)('a' + (i * 7 + oa) % 26);
                    }

                    CHECK(T->strspn((char *)s, "abcdefghijklm") == strspn((char *)s, "abcdefghijklm"));
                    CHECK(T->strcspn((char *)s, "xyz") == strcspn((char *)s, "xyz"));
                    CHECK(T->strcspn((char *)s, "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") == strcspn((char *)s, "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"));
                    CHECK(T->strspn((char *)s, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ") == strspn((char *)s, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"));
                }
            }
        }

        /* and of the set: its load has page logic of its own; the set ends before
           the guard when it fits the page, else runs into the next (mapped) one */
        unsigned char *gs2 = guard_pages(2);

        for (size_t os = 0; os < 4096; os++) {
            const size_t setlens[] = {1, 2, 15, 16, 17, 31, 32, 33, 40};

            for (size_t k = 0; k < sizeof setlens / sizeof *setlens; k++) {
                size_t m = setlens[k];
                unsigned char *set = (os + m + 1 <= 4096 ? gs + os : gs2 + os);

                for (size_t i = 0; i < m; i++) {
                    set[i] = (unsigned char)('A' + i % 26);
                }

                set[m] = 0;
                unsigned char *s = ga + 4096 + (os * 5 % 4096);

                for (size_t i = 0; i < 70; i++) {
                    s[i] = (unsigned char)(i < 20 + os % 50 ? 'A' + i % (m < 26 ? m : 26) : 'a' + i % 26);
                }

                s[70] = 0;
                CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
                CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));
                s[0] = 'z';
                CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
                CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));
            }
        }

        munmap(gs2 - 4096, 4 * 4096);
    }

    phase = "random";
    unsigned char set[520];

    for (int t = 0; t < 300000; t++) {
        size_t n = rnd() % (t % 50 == 0 ? 70000 : 400), ao = rnd() % 4096, bo = rnd() % 4096;
        unsigned char *s = a + 4096 + ao, *d = b + 4096 + bo;

        for (size_t i = 0; i < n; i++) {
            s[i] = (unsigned char)(1 + rnd() % 255);
        }

        s[n] = 0;
        CHECK(T->strlen((char *)s) == n);
        memcpy(ref, s, n + 1);
        CHECK(T->memcpy(d, s, n) == d && !memcmp(d, ref, n));
        CHECK(!T->memcmp(d, s, n));

        if (n) {
            size_t at = rnd() % n;
            d[at] = (unsigned char)(rnd() % 256);
            CHECK(sign(T->memcmp(d, s, n)) == sign(memcmp(d, s, n)));
            d[at] = (unsigned char)(1 + rnd() % 255);
            CHECK(sign(T->strcmp((char *)d, (char *)s)) == sign(strcmp((char *)d, (char *)s)));
        }

        int v = (int)(rnd() % 512) - 256;
        CHECK(T->memset(d, v, n) == d);
        memset(ref, v, n);
        CHECK(!memcmp(d, ref, n));
        /* overlap within a */
        long delta = (long)(rnd() % 2001) - 1000;
        unsigned char *od = s + delta;
        size_t span = n + (size_t)(delta < 0 ? -delta : delta);
        pattern(a, span + 8192, (unsigned)t);
        check_copy(od, s, n, ref, span);
        /* sets */
        size_t m = rnd() % 40;

        if (t % 7 == 0) {
            m = rnd() % 300;
        }

        for (size_t i = 0; i < m; i++) {
            set[i] = (unsigned char)(1 + rnd() % (t % 3 ? 60 : 255));
        }

        set[m] = 0;

        for (size_t i = 0; i < n; i++) {
            s[i] = (unsigned char)(1 + rnd() % (t % 2 ? 60 : 255));
        }

        CHECK(T->strspn((char *)s, (char *)set) == strspn((char *)s, (char *)set));
        CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));

        if (m) {
            size_t at = rnd() % (n + 1);

            if (at < n) {
                s[at] = set[rnd() % m];
                CHECK(T->strcspn((char *)s, (char *)set) == strcspn((char *)s, (char *)set));
            }
        }
    }

    printf("PASS %zu checks: small sizes x alignments, long strcat and set scans, overlap directions, large sizes, guard pages, offset pairs, single offsets, random\n", checks);
    munmap(ga - 4096, 5 * 4096);
    munmap(gb - 4096, 5 * 4096);
    munmap(gs - 4096, 3 * 4096);
    free(a); free(b); free(ref); free(ref2);
}

/* The slots of a table as an array, for the mixes. */
#define NSLOTS (sizeof(struct str_table) / sizeof(void *))
static void slots_of(const struct str_table *t, void **slots) {
    memcpy(slots, t, sizeof *t);
}

static void table_of(struct str_table *t, void *const *slots) {
    memcpy(t, slots, sizeof *t);
}

/* Other hosts, as cpuid snapshots: the decisions are pure, so they can be made
   here for machines this one is not. */
#define B(i) (1u << (i))
#define V2_ECX1 (B(0) | B(9) | B(13) | B(19) | B(20) | B(23))                     /* SSE3 SSSE3 CX16 SSE4.1 SSE4.2 POPCNT */
#define V3_ECX1 (V2_ECX1 | B(12) | B(22) | B(27) | B(28) | B(29))                /* FMA MOVBE OSXSAVE AVX F16C */
#define V3_EBX7 (B(3) | B(5) | B(8))                                             /* BMI1 AVX2 BMI2 */
#define ERMS_EBX7 B(9)
#define V4_EBX7 (V3_EBX7 | B(16) | B(17) | B(28) | B(30) | B(31))                /* AVX512 F DQ CD BW VL */
#define V2_ECX81 B(0)                                                            /* LAHF/SAHF */
#define V3_ECX81 (V2_ECX81 | B(5))                                               /* LZCNT */
#define INTEL 0x756e6547, 0x49656e69, 0x6c65746e
#define AMD 0x68747541, 0x69746e65, 0x444d4163

/* The public entry points, through the dispatched table. */
static void entries(void) {
    phase = "entry points";
    char s[64] = "hello, world", d[64];
    CHECK(str_strlen(s) == 12);
    CHECK(str_strcpy(d, s) == d && !strcmp(d, s));
    CHECK(str_strcat(d, s) == d && str_strlen(d) == 24);
    CHECK(str_strcmp(s, d) < 0 && str_strcmp(d, s) > 0 && str_strcmp(s, s) == 0);
    CHECK(str_memcpy(d, s, 13) == d && str_memcmp(d, s, 13) == 0);
    CHECK(str_memmove(d + 1, d, 13) == d + 1 && !strcmp(d + 1, s));
    CHECK(str_memset(d, 'x', 5) == d && d[0] == 'x' && d[4] == 'x' && d[5] == 'o');
    CHECK(str_memcmp(s, d, 13) < 0);
    CHECK(str_strspn(s, "ehl") == 4 && str_strcspn(s, ",") == 5);
    printf("entry points: PASS %zu checks\n", checks);
}

static void hosts(void) {
    const struct {
        const char *name;
        struct str_cpuid c;
        unsigned bits;
        const char *config;
    } machines[] = {
        {"no leaf 7", {6, INTEL, 0, 0, 0, 0, 0}, 0, "sse2"},
        {"core 2", {7, INTEL, 0x6f6, B(0) | B(9) | B(13), 0, 0, 0}, 0, "sse2"},
        {"nehalem", {7, INTEL, 0x106a5, V2_ECX1, 0, V2_ECX81, 0}, STR_SSE42, "sse42"},
        {"haswell", {7, INTEL, 0x306c3, V3_ECX1, V3_EBX7 | ERMS_EBX7, V3_ECX81, 0x7}, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"},
        {"haswell, os without ymm", {7, INTEL, 0x306c3, V3_ECX1, V3_EBX7 | ERMS_EBX7, V3_ECX81, 0x1}, STR_SSE42 | STR_ERMS, "sse42"},
        {"avx2 without erms", {7, AMD, 0x800f12, V3_ECX1, V3_EBX7, V3_ECX81, 0x7}, STR_SSE42 | STR_AVX2, "sse42"},
        {"skylake-sp", {7, INTEL, 0x50654, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ERMS, "evex"},
        {"skylake-sp, os without zmm", {7, INTEL, 0x50654, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0x7}, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"},
        {"ice lake", {7, INTEL, 0x606a6, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ZMM | STR_ERMS, "evex512"},
        {"zen 4", {7, AMD, 0xa60f12, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ZMM | STR_ERMS, "evex512"},
        {"avx-512 without erms", {7, AMD, 0xa60f12, V3_ECX1, V4_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ZMM, "sse42"},
        {"vendor like intel in ebx only", {7, 0x756e6547, 0, 0, 0x50654, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ZMM | STR_ERMS, "evex512"},
        {"vendor like intel in ebx and edx", {7, 0x756e6547, 0x49656e69, 0, 0x50654, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7}, STR_SSE42 | STR_AVX2 | STR_AVX512 | STR_ZMM | STR_ERMS, "evex512"},
    };
    phase = "hosts";

    for (size_t i = 0; i < sizeof machines / sizeof *machines; i++) {
        unsigned bits = str_host_of(&machines[i].c);

        if (bits != machines[i].bits || strcmp(str_choose_for(bits)->name, machines[i].config)) {
            fprintf(stderr, "FAIL hosts: %s: bits %#x, configuration %s\n", machines[i].name, bits, str_choose_for(bits)->name);
            exit(2);
        }

        checks++;
    }

    /* every feature bit missing on its own from a Zen 4: the level that needs it is
       lost, the ones below stay (each test of the checks fails in turn) */
    const struct { int word; unsigned bit; unsigned keep; const char *config; } one_missing[] = {
        {1, 0, STR_ERMS, "sse2"}, {1, 9, STR_ERMS, "sse2"}, {1, 19, STR_ERMS, "sse2"}, {1, 20, STR_ERMS, "sse2"},
        {1, 23, STR_ERMS, "sse2"}, {1, 13, STR_ERMS, "sse2"}, {3, 0, STR_ERMS, "sse2"},
        {1, 27, STR_SSE42 | STR_ERMS, "sse42"}, {1, 28, STR_SSE42 | STR_ERMS, "sse42"}, {1, 29, STR_SSE42 | STR_ERMS, "sse42"},
        {1, 12, STR_SSE42 | STR_ERMS, "sse42"}, {1, 22, STR_SSE42 | STR_ERMS, "sse42"}, {2, 5, STR_SSE42 | STR_ERMS, "sse42"},
        {2, 3, STR_SSE42 | STR_ERMS, "sse42"}, {2, 8, STR_SSE42 | STR_ERMS, "sse42"}, {3, 5, STR_SSE42 | STR_ERMS, "sse42"},
        {2, 16, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"}, {2, 30, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"},
        {2, 28, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"}, {2, 17, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"},
        {2, 31, STR_SSE42 | STR_AVX2 | STR_ERMS, "avx2"},
    };

    for (size_t i = 0; i < sizeof one_missing / sizeof *one_missing; i++) {
        struct str_cpuid c = {7, AMD, 0xa60f12, V3_ECX1, V4_EBX7 | ERMS_EBX7, V3_ECX81, 0xe7};
        unsigned *word = one_missing[i].word == 1 ? &c.ecx1 : one_missing[i].word == 2 ? &c.ebx7 : &c.ecx81;
        *word &= ~B(one_missing[i].bit);
        unsigned bits = str_host_of(&c);

        if (bits != one_missing[i].keep || strcmp(str_choose_for(bits)->name, one_missing[i].config)) {
            fprintf(stderr, "FAIL hosts: without word %d bit %u: bits %#x, configuration %s\n", one_missing[i].word, one_missing[i].bit, bits, str_choose_for(bits)->name);
            exit(2);
        }

        checks++;
    }

    printf("hosts: PASS %zu machines\n", checks);
}

int main(void) {
    /* every configuration the host runs, then the host's own choice */
    unsigned host = str_host();

    for (unsigned i = 0; i < str_nconfigs; i++) {
        if (str_configs[i].needs & ~host) {
            continue;
        }

        str_table = str_configs[i].table;
        Table = &str_table;
        printf("%s: ", str_configs[i].name);
        checks = 0;
        suite(1);
    }

    str_dispatch();
    Table = &str_table;
    printf("dispatched (%s): ", str_choose()->name);
    checks = 0;
    suite(1);
    /* every slot of the first and of the best configuration replaced by every
       other implementation the host can run, one at a time: the quick suite */
    const struct str_config *bases[2] = {&str_configs[0], str_choose()};
    size_t mixes = 0;
    checks = 0;

    for (int bi = 0; bi < 2; bi++) {
        void *base[NSLOTS];
        slots_of(&bases[bi]->table, base);

        for (size_t j = 0; j < NSLOTS; j++) {
            void *tried[16];
            size_t ntried = 0;

            for (unsigned k = 0; k < str_nconfigs; k++) {
                if (str_configs[k].needs & ~host) {
                    continue;
                }

                void *other[NSLOTS];
                slots_of(&str_configs[k].table, other);
                int seen = other[j] == base[j];

                for (size_t m = 0; m < ntried && !seen; m++) {
                    seen = tried[m] == other[j];
                }

                if (seen) {
                    continue;
                }

                tried[ntried++] = other[j];
                void *slots[NSLOTS];
                memcpy(slots, base, sizeof slots);
                slots[j] = other[j];
                table_of(&str_table, slots);
                Table = &str_table;
                snprintf(mix, sizeof mix, " (%s with slot %zu from %s)", bases[bi]->name, j, str_configs[k].name);
                suite(0);
                mixes++;
            }
        }
    }

    mix[0] = 0;
    printf("mixes: PASS %zu checks in %zu one-slot mixes of %s and %s\n", checks, mixes, bases[0]->name, bases[1]->name);
    checks = 0;
    hosts();
    checks = 0;
    entries();
    return 0;
}
