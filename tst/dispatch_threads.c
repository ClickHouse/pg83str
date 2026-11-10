/* A real worker starts before the default-priority dispatch constructor.
   Keep it calling the public API while immutable custom tables are published. */
#define _POSIX_C_SOURCE 200809L
#include "str.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static pthread_t worker;
static unsigned ready, stop, failed;
static unsigned long calls, seen[2];
static struct str_table custom[2];

static void fail(const char *message)
{
    fprintf(stderr, "FAIL dispatch publication: %s\n", message);
    exit(1);
}

static double now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t))
        fail("clock_gettime");
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static void wait_ready(void)
{
    double deadline = now() + 10;
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE))
        if (now() > deadline)
            fail("early worker did not enter the initial table");
}

static void *fill(void *d, int c, size_t n)
{
    unsigned char *p = d;
    for (size_t i = 0; i < n; i++)
        p[i] = (unsigned char)c;
    return d;
}

static int check(void)
{
    unsigned char s[96], d[128];
    for (unsigned i = 0; i < sizeof s; i++)
        s[i] = (unsigned char)(i + 1);
    if (str_memcpy(d, s, 63) != d)
        return 1;
    for (unsigned i = 0; i < 63; i++)
        if (d[i] != s[i])
            return 2;
    if (str_memmove(d + 1, d, 63) != d + 1)
        return 3;
    for (unsigned i = 0; i < 63; i++)
        if (d[i + 1] != s[i])
            return 4;
    if (str_memset(d, 0x1ab, 63) != d)
        return 5;
    for (unsigned i = 0; i < 63; i++)
        if (d[i] != 0xab)
            return 6;
    if (str_memcmp(s, s, 63) != 0 || str_memcmp(s, s + 1, 63) >= 0)
        return 7;
    if (str_strlen("abcdef") != 6 || str_strcmp("abc", "abd") >= 0)
        return 8;
    if (str_strcpy((char *)d, "ab") != (char *)d || d[0] != 'a' || d[1] != 'b' || d[2] != 0)
        return 9;
    if (str_strcat((char *)d, "cd") != (char *)d || d[0] != 'a' || d[1] != 'b' || d[2] != 'c' || d[3] != 'd' || d[4] != 0)
        return 10;
    if (str_strspn("abcz", "abc") != 3 || str_strcspn("abcz", "z") != 3)
        return 11;
    if (str_memchr(s, 17, 63) != s + 16)
        return 12;
    return 0;
}

static void *call_public(void *unused)
{
    (void)unused;
    /* The priority-202 constructor waits here before the default-priority
       selection can run, proving an actual live caller exists at startup. */
    if (str_current() != &str_configs[0].table || check())
        __atomic_store_n(&failed, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&stop, __ATOMIC_ACQUIRE))
    {
        const struct str_table *table = str_current();
        if (table == &custom[0])
            __atomic_fetch_add(&seen[0], 1, __ATOMIC_RELAXED);
        else if (table == &custom[1])
            __atomic_fetch_add(&seen[1], 1, __ATOMIC_RELAXED);
        else if (table != &str_configs[0].table && table != &str_choose()->table)
            __atomic_store_n(&failed, 2, __ATOMIC_RELEASE);
        if (check())
            __atomic_store_n(&failed, 3, __ATOMIC_RELEASE);
        __atomic_fetch_add(&calls, 1, __ATOMIC_RELAXED);
    }
    return NULL;
}

__attribute__((constructor(202))) static void start_early_worker(void)
{
    if (pthread_create(&worker, NULL, call_public, NULL))
        fail("pthread_create");
    wait_ready();
}

int main(void)
{
    if (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE) || str_current() != &str_choose()->table)
        fail("default constructor did not publish the host configuration");
    custom[0] = str_choose()->table;
    custom[1] = str_configs[0].table;
    custom[0].memset = fill;
    custom[1].memset = fill;
    /* Initialize both complete objects before publication, then never change
       either while a reader could still use it. The objects live to exit. */
    for (unsigned round = 0; round < 64; round++)
        for (unsigned i = 0; i < 2; i++)
        {
            unsigned long before = __atomic_load_n(&seen[i], __ATOMIC_RELAXED);
            str_select(&custom[i]);
            double deadline = now() + 10;
            while (__atomic_load_n(&seen[i], __ATOMIC_RELAXED) == before)
                if (now() > deadline || __atomic_load_n(&failed, __ATOMIC_ACQUIRE))
                    fail("reader did not observe an immutable publication");
        }
    str_dispatch();
    __atomic_store_n(&stop, 1, __ATOMIC_RELEASE);
    if (pthread_join(worker, NULL))
        fail("pthread_join");
    if (__atomic_load_n(&failed, __ATOMIC_ACQUIRE))
        fail("public API or table identity mismatch");
    printf("PASS early-constructor worker, 128 observed publications, %lu public API batches, 11 APIs, immutable one-slot mixes\n", __atomic_load_n(&calls, __ATOMIC_RELAXED));
    return 0;
}
