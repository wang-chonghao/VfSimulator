#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "acl/acl.h"
#include "runtime/kernel.h"
#include "runtime/rt.h"

namespace {

constexpr size_t kRepeatTimes = 64;
constexpr size_t kElems = 64 * kRepeatTimes;
constexpr size_t kBytes = kElems * sizeof(float);
constexpr float kAbsTol = 1e-3f;
constexpr float kRelTol = 1e-3f;

struct KernelArgs1In1Out {
    void *input0;
    void *output0;
};

struct CompareStats {
    size_t mismatchCount = 0;
    size_t firstMismatch = 0;
    float maxAbsErr = 0.0f;
    float maxRelErr = 0.0f;
    float expectedAtFirst = 0.0f;
    float actualAtFirst = 0.0f;
};

void PrintRecentAclError()
{
    const char *recent = aclGetRecentErrMsg();
    if (recent != nullptr && recent[0] != '\0') {
        std::fprintf(stderr, "[ACL] RecentErrMsg: %s\n", recent);
    }
}

bool ReadBinaryFile(const std::string &path, std::vector<unsigned char> &buffer)
{
    std::ifstream file(path, std::ios::binary);
    if (!file.is_open()) {
        std::fprintf(stderr, "[ERROR] Failed to open kernel binary: %s\n", path.c_str());
        return false;
    }
    file.seekg(0, std::ios::end);
    std::streamoff size = file.tellg();
    file.seekg(0, std::ios::beg);
    if (size <= 0) {
        std::fprintf(stderr, "[ERROR] Kernel binary is empty: %s\n", path.c_str());
        return false;
    }
    buffer.resize(static_cast<size_t>(size));
    file.read(reinterpret_cast<char *>(buffer.data()), size);
    return static_cast<bool>(file);
}

bool WriteFloatFile(const std::string &path, const std::vector<float> &data)
{
    std::ofstream file(path, std::ios::binary);
    if (!file.is_open()) {
        std::fprintf(stderr, "[WARN] Failed to write file: %s\n", path.c_str());
        return false;
    }
    file.write(reinterpret_cast<const char *>(data.data()), static_cast<std::streamsize>(data.size() * sizeof(float)));
    return static_cast<bool>(file);
}

void BuildInput(std::vector<float> &input0)
{
    for (size_t i = 0; i < input0.size(); ++i) {
        input0[i] = static_cast<float>((static_cast<int>(i % 29) - 14) * 0.125f);
    }
}

bool BuildGolden(const std::string &kernelName, const std::vector<float> &input0, std::vector<float> &golden)
{
    if (kernelName == "src_fanout_probe") {
        golden.resize(input0.size());
        for (size_t i = 0; i < input0.size(); ++i) {
            const float x = input0[i];
            golden[i] = 4.0f * x + 4.0f;
        }
        return true;
    }
    return false;
}

CompareStats CompareOutputs(const std::vector<float> &golden, const std::vector<float> &actual)
{
    CompareStats stats {};
    bool sawMismatch = false;
    for (size_t i = 0; i < golden.size() && i < actual.size(); ++i) {
        const float g = golden[i];
        const float a = actual[i];
        const float absErr = std::fabs(a - g);
        const float denom = std::max(std::fabs(g), 1.0f);
        const float relErr = absErr / denom;
        if (absErr > stats.maxAbsErr) {
            stats.maxAbsErr = absErr;
        }
        if (relErr > stats.maxRelErr) {
            stats.maxRelErr = relErr;
        }
        if (absErr > kAbsTol && relErr > kRelTol) {
            if (!sawMismatch) {
                sawMismatch = true;
                stats.firstMismatch = i;
                stats.expectedAtFirst = g;
                stats.actualAtFirst = a;
            }
            ++stats.mismatchCount;
        }
    }
    return stats;
}

#define ACL_CHECK(expr)                                                                          \
    do {                                                                                         \
        const aclError _ret = (expr);                                                            \
        if (_ret != ACL_SUCCESS) {                                                               \
            std::fprintf(stderr, "[ERROR] %s failed: %d (%s:%d)\n", #expr, (int)_ret, __FILE__, \
                         __LINE__);                                                              \
            PrintRecentAclError();                                                               \
            rc = 1;                                                                              \
            goto cleanup;                                                                        \
        }                                                                                        \
    } while (0)

#define RT_CHECK(expr)                                                                           \
    do {                                                                                         \
        const rtError_t _ret = (expr);                                                           \
        if (_ret != RT_ERROR_NONE) {                                                             \
            std::fprintf(stderr, "[ERROR] %s failed: %d (%s:%d)\n", #expr, (int)_ret, __FILE__, \
                         __LINE__);                                                              \
            rc = 2;                                                                              \
            goto cleanup;                                                                        \
        }                                                                                        \
    } while (0)

} // namespace

int main(int argc, char **argv)
{
    if (argc < 3) {
        std::fprintf(stderr, "Usage: %s <kernel-bin> <kernel-name>\n", argv[0]);
        return 64;
    }

    const std::string kernelBinPath = argv[1];
    const std::string kernelName = argv[2];

    int rc = 0;
    bool aclInited = false;
    bool deviceSet = false;
    int deviceId = 0;

    rtStream_t stream = nullptr;
    void *binHandle = nullptr;
    void *stubFunc = nullptr;
    void *input0Dev = nullptr;
    void *output0Dev = nullptr;

    rtDevBinary_t binary {};
    KernelArgs1In1Out args {};

    std::vector<unsigned char> kernelBuffer;
    std::vector<float> input0Host(kElems);
    std::vector<float> outputHost(kElems, 0.0f);
    std::vector<float> golden;

    BuildInput(input0Host);

    if (const char *envDevice = std::getenv("ACL_DEVICE_ID")) {
        deviceId = std::atoi(envDevice);
    }
    if (!ReadBinaryFile(kernelBinPath, kernelBuffer)) {
        return 65;
    }

    ACL_CHECK(aclInit(nullptr));
    aclInited = true;
    RT_CHECK(rtSetDevice(deviceId));
    deviceSet = true;
    RT_CHECK(rtStreamCreate(&stream, 0));

    RT_CHECK(rtMalloc(&input0Dev, kBytes, RT_MEMORY_HBM, 0));
    RT_CHECK(rtMalloc(&output0Dev, kBytes, RT_MEMORY_HBM, 0));
    RT_CHECK(rtMemcpy(input0Dev, kBytes, input0Host.data(), kBytes, RT_MEMCPY_HOST_TO_DEVICE));

    binary.magic = RT_DEV_BINARY_MAGIC_ELF_AIVEC;
    binary.version = 0;
    binary.length = static_cast<uint64_t>(kernelBuffer.size());
    binary.data = kernelBuffer.data();

    RT_CHECK(rtDevBinaryRegister(&binary, &binHandle));
    RT_CHECK(rtFunctionRegister(binHandle,
                                reinterpret_cast<const void *>(kernelName.c_str()),
                                reinterpret_cast<const char_t *>(kernelName.c_str()),
                                reinterpret_cast<const void *>(kernelName.c_str()),
                                0));
    RT_CHECK(rtGetFunctionByName(reinterpret_cast<const char_t *>(kernelName.c_str()), &stubFunc));

    args.input0 = input0Dev;
    args.output0 = output0Dev;

    RT_CHECK(rtKernelLaunch(stubFunc, 1, &args, static_cast<uint32_t>(sizeof(args)), nullptr, stream));
    RT_CHECK(rtStreamSynchronize(stream));
    RT_CHECK(rtMemcpy(outputHost.data(), kBytes, output0Dev, kBytes, RT_MEMCPY_DEVICE_TO_HOST));

    std::printf("[INFO] native runtime sim finished for %s\n", kernelName.c_str());
    for (int i = 0; i < 8; ++i) {
        std::printf("  out[%d] = %.8f\n", i, outputHost[static_cast<size_t>(i)]);
    }

    (void)WriteFloatFile("input0.bin", input0Host);
    (void)WriteFloatFile("output0.bin", outputHost);

    if (BuildGolden(kernelName, input0Host, golden)) {
        (void)WriteFloatFile("golden.bin", golden);
        const CompareStats stats = CompareOutputs(golden, outputHost);
        std::printf("[CHECK] kernel=%s mismatches=%zu max_abs_err=%.8g max_rel_err=%.8g\n",
                    kernelName.c_str(), stats.mismatchCount, stats.maxAbsErr, stats.maxRelErr);
        if (stats.mismatchCount > 0) {
            std::printf("[CHECK] first_mismatch idx=%zu expected=%.8f actual=%.8f\n",
                        stats.firstMismatch, stats.expectedAtFirst, stats.actualAtFirst);
            rc = 3;
        } else {
            std::printf("[CHECK] PASS\n");
        }
    } else {
        std::printf("[CHECK] No built-in golden rule for kernel %s, skipped.\n", kernelName.c_str());
    }

cleanup:
    if (output0Dev != nullptr) {
        (void)rtFree(output0Dev);
    }
    if (input0Dev != nullptr) {
        (void)rtFree(input0Dev);
    }
    if (stream != nullptr) {
        (void)rtStreamDestroy(stream);
    }
    if (deviceSet) {
        (void)rtDeviceReset(deviceId);
    }
    if (aclInited) {
        (void)aclFinalize();
    }
    return rc;
}
