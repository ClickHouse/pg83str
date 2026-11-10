#define _GNU_SOURCE
#include "str.h"
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <libunwind.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <ucontext.h>

enum { CASES = 7, SAMPLES_PER_CASE = 128, MAX_SAMPLES = 32 * CASES * SAMPLES_PER_CASE, FRAMES = 48 };

struct sample
{
    uintptr_t interrupted;
    void * frames[FRAMES];
    int count;
    unsigned config;
    unsigned test;
};

static struct sample samples[MAX_SAMPLES];
static volatile sig_atomic_t sample_count;
static volatile sig_atomic_t case_count;
static volatile sig_atomic_t active_config;
static volatile sig_atomic_t active_case;
static volatile uint64_t consumed;
static unsigned char * source;
static unsigned char * destination;
static const size_t capacity = (1U << 20) + 4096;

static void capture(int signal_number, siginfo_t * info, void * context)
{
    (void)signal_number;
    (void)info;
    sig_atomic_t index = sample_count;
    if (index >= MAX_SAMPLES)
        _Exit(90);
    struct sample * sample = &samples[index];
    sample->interrupted = (uintptr_t)((ucontext_t *)context)->uc_mcontext.gregs[REG_RIP];
    sample->config = (unsigned)active_config;
    sample->test = (unsigned)active_case;
    sample->count = unw_backtrace(sample->frames, FRAMES);
    sample_count = index + 1;
    ++case_count;
}

__attribute__((noinline)) static void run_case(unsigned test)
{
    uint64_t value = consumed;
    size_t iteration = 0;
    while (case_count < SAMPLES_PER_CASE)
    {
        size_t n;
        switch (test)
        {
            case 0:
                n = (iteration * 13U) & 15U;
                value ^= (uintptr_t)str_memcpy(destination + 3, source + 5, n);
                value += destination[3];
                break;
            case 1:
                n = 17U + ((iteration * 37U) % 112U);
                value ^= (uintptr_t)str_memcpy(destination + 3, source + 5, n);
                value += destination[n + 2];
                break;
            case 2:
                n = 257U + ((iteration * 37U) % 3840U);
                value ^= (uintptr_t)str_memmove(destination + 17, destination + 3, n);
                value += destination[n + 16];
                break;
            case 3:
                value ^= (uintptr_t)str_memcpy(destination + 3, source + 5, 1U << 20);
                value += destination[999];
                break;
            case 4:
                value ^= (uintptr_t)str_memset(destination + 3, (int)iteration, 1U << 20);
                value += destination[999];
                break;
            case 5:
                value += (unsigned)str_memcmp(source + 5, source + 5, 1U << 20);
                break;
            case 6:
                value += str_strlen((char *)source + 5);
                break;
            default:
                _Exit(91);
        }
        ++iteration;
        __asm__ volatile ("" : "+r"(value) : : "memory");
    }
    consumed = value;
}

int main(void)
{
    assert(posix_memalign((void **)&source, 64, capacity) == 0);
    assert(posix_memalign((void **)&destination, 64, capacity) == 0);
    memset(source, 0x41, capacity);
    memset(destination, 0x42, capacity);
    source[capacity - 1] = 0;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = capture;
    action.sa_flags = SA_SIGINFO;
    assert(sigemptyset(&action.sa_mask) == 0);
    assert(sigaction(SIGPROF, &action, NULL) == 0);
    sigset_t timer_signal;
    assert(sigemptyset(&timer_signal) == 0);
    assert(sigaddset(&timer_signal, SIGPROF) == 0);
    assert(sigprocmask(SIG_BLOCK, &timer_signal, NULL) == 0);
    void * warm[FRAMES];
    assert(unw_backtrace(warm, FRAMES) > 0);
    unsigned host = str_host();
    Dl_info image;
    assert(dladdr((void *)(uintptr_t)&main, &image) != 0);
    printf("META\t%" PRIxPTR "\t%u\t%u\n", (uintptr_t)image.dli_fbase, host, str_nconfigs);
    unsigned visited = 0;
    for (unsigned config = 0; config < str_nconfigs; ++config)
    {
        if ((str_configs[config].needs & host) != str_configs[config].needs)
            continue;
        str_table = str_configs[config].table;
        ++visited;
        for (unsigned test = 0; test < CASES; ++test)
        {
            active_config = (sig_atomic_t)config;
            active_case = (sig_atomic_t)test;
            case_count = 0;
            struct itimerval timer = {{0, 500}, {0, 500}};
            assert(setitimer(ITIMER_PROF, &timer, NULL) == 0);
            assert(sigprocmask(SIG_UNBLOCK, &timer_signal, NULL) == 0);
            run_case(test);
            assert(sigprocmask(SIG_BLOCK, &timer_signal, NULL) == 0);
            struct itimerval stopped = {{0, 0}, {0, 0}};
            assert(setitimer(ITIMER_PROF, &stopped, NULL) == 0);
            const struct timespec no_wait = {0, 0};
            unsigned pending_not_delivered = 0;
            int result;
            while ((result = sigtimedwait(&timer_signal, NULL, &no_wait)) == SIGPROF)
                ++pending_not_delivered;
            assert(result == -1 && errno == EAGAIN);
            printf("CASE\t%u\t%s\t%u\t%d\t%u\n", config, str_configs[config].name, test, (int)case_count, pending_not_delivered);
        }
    }
    for (sig_atomic_t i = 0; i < sample_count; ++i)
    {
        const struct sample * sample = &samples[i];
        printf("TRACE\t%u\t%u\t%" PRIxPTR "\t%d", sample->config, sample->test, sample->interrupted, sample->count);
        for (int frame = 0; frame < sample->count; ++frame)
            printf("\t%" PRIxPTR, (uintptr_t)sample->frames[frame]);
        putchar('\n');
    }
    printf("DONE\t%u\t%d\t%" PRIu64 "\n", visited, (int)sample_count, consumed);
    free(source);
    free(destination);
}
