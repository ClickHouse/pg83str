#ifndef PG83_AARCH64_SELECTED_STR_H
#define PG83_AARCH64_SELECTED_STR_H

#include <stddef.h>

#ifdef __cplusplus
extern "C"
{
#endif

void *str_memcpy(void *, const void *, size_t);
void *str_memmove(void *, const void *, size_t);
void *str_memset(void *, int, size_t);
void *str_memchr(const void *, int, size_t);
int str_memcmp(const void *, const void *, size_t);
size_t str_strlen(const char *);
char *str_strcpy(char *, const char *);
char *str_strcat(char *, const char *);
int str_strcmp(const char *, const char *);
size_t str_strspn(const char *, const char *);
size_t str_strcspn(const char *, const char *);

#ifdef __cplusplus
}
#endif

#endif
