#include <cstddef>
#include <cstring>

extern "C" int memcpy_s(void *dest, size_t destMax, const void *src, size_t count);
extern "C" int memset_s(void *dest, size_t destMax, int c, size_t count);

extern "C" int safe_memcpy_with_check(void *dest, size_t destMax, const void *src, size_t count)
    asm("_Z22safe_memcpy_with_checkPvmPKvm");
extern "C" int safe_memset_with_check(void *dest, size_t destMax, int c, size_t count)
    asm("_Z22safe_memset_with_checkPvmim");

extern "C" int safe_memcpy_with_check(void *dest, size_t destMax, const void *src, size_t count)
{
    if (dest == nullptr || src == nullptr) {
        return -1;
    }
    if (count > destMax) {
        return -1;
    }
    return memcpy_s(dest, destMax, src, count);
}

extern "C" int safe_memset_with_check(void *dest, size_t destMax, int c, size_t count)
{
    if (dest == nullptr) {
        return -1;
    }
    if (count > destMax) {
        return -1;
    }
    return memset_s(dest, destMax, c, count);
}