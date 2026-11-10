#include "kernels.h"
#include <sys/auxv.h>
#include <asm/hwcap.h>

#define TABLE(suffix) { \
    .memcpy = memcpy_##suffix, .memmove = memmove_##suffix, .memset = memset_##suffix, .memcmp = memcmp_##suffix, \
    .strlen = strlen_##suffix, .strcpy = strcpy_##suffix, .strcat = strcat_##suffix, .strcmp = strcmp_##suffix, \
    .strspn = strspn_##suffix, .strcspn = strcspn_##suffix, .memchr = memchr_##suffix, \
    .copy_large = copy_large_##suffix, .fill_large = fill_large_##suffix, .memcmp_large = memcmp_large_##suffix, \
    .strlen_tail = strlen_tail_##suffix, .strcmp_tail = strcmp_tail_##suffix, .strcpy_long = strcpy_long_##suffix, \
    .copy_huge = copy_huge_##suffix, .fill_huge = fill_huge_##suffix, .memchr_large = memchr_large_##suffix \
}

const struct str_config str_configs[] = {
    {"scalar", 0, TABLE(scalar)},
    {"neon", STR_NEON, TABLE(neon)},
};

const unsigned str_nconfigs = sizeof str_configs / sizeof str_configs[0];

unsigned str_host(void) {
    return getauxval(AT_HWCAP) & HWCAP_ASIMD ? STR_NEON : 0;
}

const struct str_config *str_choose_for(unsigned host) {
    /* Keep the production choice at the scalar baseline while the NEON
       candidate is being measured and its machine-code verifier is ported. */
    (void)host;
    return &str_configs[0];
}

const struct str_config *str_choose(void) {
    return str_choose_for(str_host());
}

static const struct str_table *str_active_table = &str_configs[0].table;
_Static_assert(__atomic_always_lock_free(sizeof(str_active_table), 0), "dispatch requires lock-free pointers");

static inline const struct str_table *current_table(void)
{
    return __atomic_load_n(&str_active_table, __ATOMIC_ACQUIRE);
}

const struct str_table *str_current(void)
{
    return current_table();
}

void str_select(const struct str_table *table)
{
    __atomic_store_n(&str_active_table, table, __ATOMIC_RELEASE);
}

void str_dispatch(void) {
    str_select(&str_choose()->table);
}

__attribute__((constructor)) static void select_table(void) {
    str_dispatch();
}

void *str_memcpy(void *d, const void *s, size_t n) {
    return current_table()->memcpy(d, s, n);
}

void *str_memmove(void *d, const void *s, size_t n) {
    return current_table()->memmove(d, s, n);
}

void *str_memset(void *d, int c, size_t n) {
    return current_table()->memset(d, c, n);
}

int str_memcmp(const void *a, const void *b, size_t n) {
    return current_table()->memcmp(a, b, n);
}

size_t str_strlen(const char *s) {
    return current_table()->strlen(s);
}

char *str_strcpy(char *d, const char *s) {
    return current_table()->strcpy(d, s);
}

char *str_strcat(char *d, const char *s) {
    return current_table()->strcat(d, s);
}

int str_strcmp(const char *a, const char *b) {
    return current_table()->strcmp(a, b);
}

size_t str_strspn(const char *s, const char *set) {
    return current_table()->strspn(s, set);
}

size_t str_strcspn(const char *s, const char *set) {
    return current_table()->strcspn(s, set);
}

void *str_memchr(const void *s, int c, size_t n) {
    return current_table()->memchr(s, c, n);
}

void *str_copy_large(u8 *d, const u8 *s, size_t n) {
    return current_table()->copy_large(d, s, n);
}

void *str_fill_large(u8 *d, int c, size_t n) {
    return current_table()->fill_large(d, c, n);
}

int str_memcmp_large(const u8 *a, const u8 *b, size_t n) {
    return current_table()->memcmp_large(a, b, n);
}

size_t str_strlen_tail(const u8 *a, const u8 *s) {
    return current_table()->strlen_tail(a, s);
}

int str_strcmp_tail(const u8 *a, const u8 *b) {
    return current_table()->strcmp_tail(a, b);
}

void *str_strcpy_long(u8 *d, const u8 *s, const u8 *a) {
    return current_table()->strcpy_long(d, s, a);
}

void *str_copy_huge(u8 *d, const u8 *s, size_t n) {
    return current_table()->copy_huge(d, s, n);
}

void *str_fill_huge(u8 *d, int c, size_t n) {
    return current_table()->fill_huge(d, c, n);
}

void *str_memchr_large(const u8 *s, u8 c, size_t n) {
    return current_table()->memchr_large(s, c, n);
}
