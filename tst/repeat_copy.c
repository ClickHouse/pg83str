/* The copy caller in `repeat`: 14-byte strings, repeated 70 times in blocks
   of 1024 rows. This excludes SQL generation and UTF-8 counting. */
#define _POSIX_C_SOURCE 200809L
#include "../str.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum { ROWS = 700000, BLOCK = 1024, WIDTH = 14, REPEAT = 70 };
static volatile unsigned long long witness;

static double now(void)
{
    struct timespec value;
    assert(clock_gettime(CLOCK_MONOTONIC_RAW, &value) == 0);
    return value.tv_sec + value.tv_nsec * 1e-9;
}

static void produce(unsigned char *out, const unsigned char *input)
{
    for (size_t row = 0; row < ROWS; ++row)
    {
        unsigned char *dst = out + (row % BLOCK) * WIDTH * REPEAT;
        const unsigned char *head = dst;
        unsigned long long count = REPEAT;
        unsigned long long bit = count & 1;
        unsigned int k = 0;
        count >>= 1;
        str_memcpy(dst, input + (row % BLOCK) * WIDTH, WIDTH);
        dst += WIDTH;
        while (count > 0)
        {
            size_t bytes = WIDTH * ((size_t)1 << k);
            str_memcpy(dst, head, bytes);
            dst += bytes;
            if (bit)
            {
                str_memcpy(dst, head, bytes);
                dst += bytes;
            }
            ++k;
            bit = count & 1;
            count >>= 1;
        }
    }
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    const struct str_config *selected = NULL;
    for (unsigned int i = 0; i < str_nconfigs; ++i)
    {
        if (strcmp(str_configs[i].name, argv[1]) == 0)
            selected = &str_configs[i];
    }
    assert(selected != NULL);
    const unsigned int isa = STR_SSE42 | STR_AVX2 | STR_AVX512;
    assert((selected->needs & isa & ~str_host()) == 0);
    str_select(&selected->table);

    unsigned char *input = NULL, *out = NULL, *expected = NULL;
    assert(posix_memalign((void **)&input, 64, BLOCK * WIDTH) == 0);
    assert(posix_memalign((void **)&out, 64, BLOCK * WIDTH * REPEAT) == 0);
    assert(posix_memalign((void **)&expected, 64, BLOCK * WIDTH * REPEAT) == 0);
    for (size_t row = 0; row < BLOCK; ++row)
    {
        for (size_t col = 0; col < WIDTH; ++col)
            input[row * WIDTH + col] = (unsigned char)(1 + (row * 17 + col * 29) % 251);
        for (size_t col = 0; col < WIDTH * REPEAT; ++col)
            expected[row * WIDTH * REPEAT + col] = input[row * WIDTH + col % WIDTH];
    }

    printf("profile=%s rows=%d block=%d width=%d repeat=%d calls_per_row=9 public_entry=1\n",
        selected->name, ROWS, BLOCK, WIDTH, REPEAT);
    for (int round = -2; round < 9; ++round)
    {
        double start = now();
        produce(out, input);
        double seconds = now() - start;
        assert(memcmp(out, expected, BLOCK * WIDTH * REPEAT) == 0);
        witness += out[(unsigned int)(round + 2) % (BLOCK * WIDTH * REPEAT)];
        if (round >= 0)
            printf("sample\t%d\t%.9f\n", round, seconds);
    }
    printf("PASS all output bytes; witness=%llu\n", witness);
    free(input);
    free(out);
    free(expected);
    return 0;
}
