/* The prover's oracle: the library's functions run natively on inputs
   replay.py sends, to check the prover's machine (the instruction
   semantics) against the hardware. Two data pages between two PROT_NONE
   pages; per line: a function's symbol, its arguments (pN: the data's
   address plus N, iN: the number N) and the two pages' bytes in hex; the
   answer: the result (rax) in hex and the two pages after the call. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

struct oracle_fn {
    const char *name;
    void *fn;
};

extern const struct oracle_fn oracle_fns[];

typedef uint64_t (*fn6)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);

static int hexval(int c) {
    return c <= '9' ? c - '0' : (c | 32) - 'a' + 10;
}

int main(void) {
    unsigned char *m = mmap(0, 4 * 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    if (m == MAP_FAILED || mprotect(m + 4096, 2 * 4096, PROT_READ | PROT_WRITE)) {
        return 2;
    }

    unsigned char *data = m + 4096;
    static char line[1 << 16];
    printf("base %llx\n", (unsigned long long)(uintptr_t)data);
    fflush(stdout);

    while (fgets(line, sizeof line, stdin)) {
        char *sv, *name = strtok_r(line, " \n", &sv);
        uint64_t a[6] = {0};
        int n = 0;
        char *tok;
        void *fn = 0;

        for (int i = 0; name && oracle_fns[i].name; i++) {
            if (!strcmp(oracle_fns[i].name, name)) {
                fn = oracle_fns[i].fn;
            }
        }

        if (!fn) {
            printf("unknown %s\n", name ? name : "");
            fflush(stdout);
            continue;
        }

        while ((tok = strtok_r(0, " \n", &sv)) && tok[0] != 'x' && n < 6) {
            a[n++] = tok[0] == 'p' ? (uint64_t)(uintptr_t)(data + strtoull(tok + 1, 0, 0)) : strtoull(tok + 1, 0, 0);
        }

        if (!tok || strlen(tok + 1) != 2 * 2 * 4096) {
            printf("bad data\n");
            fflush(stdout);
            continue;
        }

        for (int i = 0; i < 2 * 4096; i++) {
            data[i] = (unsigned char)(hexval(tok[1 + 2 * i]) << 4 | hexval(tok[2 + 2 * i]));
        }

        uint64_t r = ((fn6)fn)(a[0], a[1], a[2], a[3], a[4], a[5]);
        printf("%llx ", (unsigned long long)r);

        for (int i = 0; i < 2 * 4096; i++) {
            printf("%02x", data[i]);
        }

        printf("\n");
        fflush(stdout);
    }

    return 0;
}
