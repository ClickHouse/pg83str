/* Public short comparison entry with compiler-generated unwind information. */
#include "kernels.h"
#include "aarch64_helpers.h"

static inline int compare_word(uint64_t a, uint64_t b)
{
    a = __builtin_bswap64(a);
    b = __builtin_bswap64(b);
    return (a > b) - (a < b);
}

int str_memcmp(const void *x, const void *y, size_t n)
{
    const u8 *a = x, *b = y;

    if (n <= 16)
    {
        if (n >= 8)
        {
            uint64_t ah = kl_load64(a), bh = kl_load64(b);
            uint64_t at = kl_load64(a + n - 8), bt = kl_load64(b + n - 8);
            return compare_word(ah != bh ? ah : at, ah != bh ? bh : bt);
        }

        if (n >= 4)
        {
            uint32_t ah = __builtin_bswap32(kl_load32(a)), bh = __builtin_bswap32(kl_load32(b));
            uint32_t at = __builtin_bswap32(kl_load32(a + n - 4)), bt = __builtin_bswap32(kl_load32(b + n - 4));
            uint32_t av = ah != bh ? ah : at, bv = ah != bh ? bh : bt;
            return (av > bv) - (av < bv);
        }

        if (!n)
        {
            return 0;
        }

        int h = (int)a[0] - b[0], m = (int)a[n / 2] - b[n / 2], t = (int)a[n - 1] - b[n - 1];
        return h ? h : m ? m : t;
    }

    return kl_memcmp_neon_words(x, y, n);
}
