#ifndef STR_AARCH64_KERNELS_H
#define STR_AARCH64_KERNELS_H
#include "../str.h"

#define STR_DECLARE_KERNELS(suffix) \
    void *memcpy_##suffix(void *, const void *, size_t); \
    void *memmove_##suffix(void *, const void *, size_t); \
    void *memset_##suffix(void *, int, size_t); \
    int memcmp_##suffix(const void *, const void *, size_t); \
    size_t strlen_##suffix(const char *); \
    char *strcpy_##suffix(char *, const char *); \
    char *strcat_##suffix(char *, const char *); \
    int strcmp_##suffix(const char *, const char *); \
    size_t strspn_##suffix(const char *, const char *); \
    size_t strcspn_##suffix(const char *, const char *); \
    void *memchr_##suffix(const void *, int, size_t); \
    void *copy_large_##suffix(u8 *, const u8 *, size_t); \
    void *fill_large_##suffix(u8 *, int, size_t); \
    int memcmp_large_##suffix(const u8 *, const u8 *, size_t); \
    size_t strlen_tail_##suffix(const u8 *, const u8 *); \
    int strcmp_tail_##suffix(const u8 *, const u8 *); \
    void *strcpy_long_##suffix(u8 *, const u8 *, const u8 *); \
    void *copy_huge_##suffix(u8 *, const u8 *, size_t); \
    void *fill_huge_##suffix(u8 *, int, size_t); \
    void *memchr_large_##suffix(const u8 *, u8, size_t)

STR_DECLARE_KERNELS(scalar);
STR_DECLARE_KERNELS(neon);
#undef STR_DECLARE_KERNELS
#endif
