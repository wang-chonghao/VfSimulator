#include <cstdio>
#include <cstdlib>
#include <vector>

#include "acl/acl.h"

namespace {

constexpr size_t kElemCount = 16 * 64;
constexpr size_t kBytes = kElemCount * sizeof(float);

#define ACL_CHECK(expr)                                                                          \
    do {                                                                                         \
        const aclError _ret = (expr);                                                            \
        if (_ret != ACL_SUCCESS) {                                                               \
            std::fprintf(stderr, "[ERROR] %s failed: %d (%s:%d)\n", #expr, (int)_ret, __FILE__, \
                         __LINE__);                                                              \
            const char *_recent = aclGetRecentErrMsg();                                          \
            if (_recent != nullptr && _recent[0] != '\0') {                                      \
                std::fprintf(stderr, "[ERROR] RecentErrMsg: %s\n", _recent);                    \
            }                                                                                    \
            rc = 1;                                                                              \
            goto cleanup;                                                                        \
        }                                                                                        \
    } while (0)

} // namespace

void LaunchKernel(void *stream, float *input0, float *input1, float *output0);

int main()
{
    int rc = 0;
    bool aclInited = false;
    bool deviceSet = false;
    int deviceId = 0;
    aclrtStream stream = nullptr;

    float *input0Dev = nullptr;
    float *input1Dev = nullptr;
    float *outputDev = nullptr;

    std::vector<float> input0Host(kElemCount);
    std::vector<float> input1Host(kElemCount, 0.0f);
    std::vector<float> outputHost(kElemCount, 0.0f);

    for (size_t i = 0; i < kElemCount; ++i) {
        input0Host[i] = static_cast<float>((static_cast<int>(i % 17) - 8) * 0.25f);
    }

    ACL_CHECK(aclInit(nullptr));
    aclInited = true;
    if (const char *envDevice = std::getenv("ACL_DEVICE_ID")) {
        deviceId = std::atoi(envDevice);
    }
    ACL_CHECK(aclrtSetDevice(deviceId));
    deviceSet = true;
    ACL_CHECK(aclrtCreateStream(&stream));

    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&input0Dev), kBytes, ACL_MEM_MALLOC_NORMAL_ONLY));
    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&input1Dev), kBytes, ACL_MEM_MALLOC_NORMAL_ONLY));
    ACL_CHECK(aclrtMalloc(reinterpret_cast<void **>(&outputDev), kBytes, ACL_MEM_MALLOC_NORMAL_ONLY));

    ACL_CHECK(aclrtMemcpy(input0Dev, kBytes, input0Host.data(), kBytes, ACL_MEMCPY_HOST_TO_DEVICE));
    ACL_CHECK(aclrtMemcpy(input1Dev, kBytes, input1Host.data(), kBytes, ACL_MEMCPY_HOST_TO_DEVICE));

    LaunchKernel(stream, input0Dev, input1Dev, outputDev);
    ACL_CHECK(aclrtSynchronizeStream(stream));

    ACL_CHECK(aclrtMemcpy(outputHost.data(), kBytes, outputDev, kBytes, ACL_MEMCPY_DEVICE_TO_HOST));

    std::printf("[INFO] GeLU sim finished. First 8 outputs:\n");
    for (int i = 0; i < 8; ++i) {
        std::printf("  out[%d] = %.8f\n", i, outputHost[static_cast<size_t>(i)]);
    }

cleanup:
    if (outputDev != nullptr) (void)aclrtFree(outputDev);
    if (input1Dev != nullptr) (void)aclrtFree(input1Dev);
    if (input0Dev != nullptr) (void)aclrtFree(input0Dev);
    if (stream != nullptr) (void)aclrtDestroyStream(stream);
    if (deviceSet) (void)aclrtResetDevice(deviceId);
    if (aclInited) (void)aclFinalize();
    return rc;
}
