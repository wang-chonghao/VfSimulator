#include <algorithm>
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

constexpr float kAbsTol = 1e-3f;
constexpr float kRelTol = 1e-3f;

struct CompareStats {
    size_t mismatchCount = 0;
    size_t firstMismatch = 0;
    float maxAbsErr = 0.0f;
    float maxRelErr = 0.0f;
    float expectedAtFirst = 0.0f;
    float actualAtFirst = 0.0f;
};

struct KernelConfig {
    size_t numInputs = 1;
    size_t numOutputs = 1;
    size_t totalElems = 0;
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

int ReadIntEnv(const char *name, int fallback)
{
    if (const char *value = std::getenv(name)) {
        return std::atoi(value);
    }
    return fallback;
}

bool InferKernelConfig(const std::string &kernelName, KernelConfig &cfg)
{
    if (kernelName == "src_fanout_probe") {
        cfg.numInputs = 1;
        cfg.numOutputs = 1;
        cfg.totalElems = 64 * 64;
        return true;
    }
    if (kernelName == "foo_add") {
        cfg.numInputs = 2;
        cfg.numOutputs = 1;
        cfg.totalElems = 64 * 16;
        return true;
    }
    return false;
}

void FillInput(std::vector<float> &input, size_t inputIndex)
{
    for (size_t i = 0; i < input.size(); ++i) {
        if (inputIndex == 0) {
            input[i] = static_cast<float>((static_cast<int>(i % 29) - 14) * 0.125f);
        } else if (inputIndex == 1) {
            input[i] = static_cast<float>((static_cast<int>(i % 17) - 8) * 0.0625f);
        } else {
            input[i] = static_cast<float>((static_cast<int>((i + inputIndex) % 23) - 11) * 0.03125f);
        }
    }
}

void BuildSrcFanoutGolden(const std::vector<float> &input0, std::vector<float> &golden)
{
    golden.resize(input0.size());
    for (size_t i = 0; i < input0.size(); ++i) {
        golden[i] = 4.0f * input0[i] + 4.0f;
    }
}

void BuildGeluPolyGolden(const std::vector<float> &input0, std::vector<float> &golden)
{
    golden.resize(input0.size());
    for (size_t i = 0; i < input0.size(); ++i) {
        const float x = input0[i];
        const float xHalf = 0.5f * x;
        float t = 0.7071f * x;
        t = std::fmin(t, 3.92f);
        t = std::fmax(t, -3.92f);
        const float t2 = t * t;

        float num = 0.5344f * t2 + 7.5517f;
        num = num * t2 + 101.62809f;
        num = num * t2 + 1393.8015f;
        num = num * t2 + 5063.7915f;
        num = num * t2 + 29639.3848f;
        num = num * t;

        float den = t2 + 31.2128582f;
        den = den * t2 + 308.569641f;
        den = den * t2 + 3023.12476f;
        den = den * t2 + 14243.3662f;
        den = den * t2 + 26267.2246f;

        float y = num / den;
        y = (y + 1.0f) * xHalf;
        golden[i] = y;
    }
}

bool BuildGolden(const std::string &kernelName, const std::vector<std::vector<float>> &inputs, std::vector<float> &golden)
{
    if (inputs.empty()) {
        return false;
    }
    if (kernelName == "src_fanout_probe") {
        BuildSrcFanoutGolden(inputs[0], golden);
        return true;
    }
    if (kernelName == "foo_add") {
        BuildGeluPolyGolden(inputs[0], golden);
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
        stats.maxAbsErr = std::max(stats.maxAbsErr, absErr);
        stats.maxRelErr = std::max(stats.maxRelErr, relErr);
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
        std::fprintf(stderr, "Usage: %s <kernel-bin> <kernel-name> [num-inputs] [num-outputs] [total-elems]\n", argv[0]);
        return 64;
    }

    const std::string kernelBinPath = argv[1];
    const std::string kernelName = argv[2];

    KernelConfig cfg {};
    if (!InferKernelConfig(kernelName, cfg)) {
        cfg.numInputs = 1;
        cfg.numOutputs = 1;
        cfg.totalElems = 64 * 16;
    }

    if (argc >= 4) {
        cfg.numInputs = static_cast<size_t>(std::strtoul(argv[3], nullptr, 10));
    } else {
        cfg.numInputs = static_cast<size_t>(ReadIntEnv("NUM_INPUTS", static_cast<int>(cfg.numInputs)));
    }
    if (argc >= 5) {
        cfg.numOutputs = static_cast<size_t>(std::strtoul(argv[4], nullptr, 10));
    } else {
        cfg.numOutputs = static_cast<size_t>(ReadIntEnv("NUM_OUTPUTS", static_cast<int>(cfg.numOutputs)));
    }
    if (argc >= 6) {
        cfg.totalElems = static_cast<size_t>(std::strtoul(argv[5], nullptr, 10));
    } else {
        cfg.totalElems = static_cast<size_t>(ReadIntEnv("TOTAL_ELEMS", static_cast<int>(cfg.totalElems)));
    }

    if (cfg.numInputs == 0 || cfg.numOutputs == 0 || cfg.totalElems == 0) {
        std::fprintf(stderr, "[ERROR] Invalid kernel config: inputs=%zu outputs=%zu elems=%zu\n",
                     cfg.numInputs, cfg.numOutputs, cfg.totalElems);
        return 65;
    }

    const size_t bytes = cfg.totalElems * sizeof(float);

    int rc = 0;
    bool aclInited = false;
    bool deviceSet = false;
    int deviceId = 0;

    rtStream_t stream = nullptr;
    void *binHandle = nullptr;
    void *stubFunc = nullptr;

    std::vector<void *> inputDevs(cfg.numInputs, nullptr);
    std::vector<void *> outputDevs(cfg.numOutputs, nullptr);
    rtDevBinary_t binary {};

    std::vector<unsigned char> kernelBuffer;
    std::vector<std::vector<float>> inputs(cfg.numInputs, std::vector<float>(cfg.totalElems));
    std::vector<std::vector<float>> outputs(cfg.numOutputs, std::vector<float>(cfg.totalElems, 0.0f));
    std::vector<float> golden;
    std::vector<void *> argPtrs;

    for (size_t i = 0; i < cfg.numInputs; ++i) {
        FillInput(inputs[i], i);
    }

    if (const char *envDevice = std::getenv("ACL_DEVICE_ID")) {
        deviceId = std::atoi(envDevice);
    }
    if (!ReadBinaryFile(kernelBinPath, kernelBuffer)) {
        return 66;
    }

    ACL_CHECK(aclInit(nullptr));
    aclInited = true;
    RT_CHECK(rtSetDevice(deviceId));
    deviceSet = true;
    RT_CHECK(rtStreamCreate(&stream, 0));

    for (size_t i = 0; i < cfg.numInputs; ++i) {
        RT_CHECK(rtMalloc(&inputDevs[i], bytes, RT_MEMORY_HBM, 0));
        RT_CHECK(rtMemcpy(inputDevs[i], bytes, inputs[i].data(), bytes, RT_MEMCPY_HOST_TO_DEVICE));
    }
    for (size_t i = 0; i < cfg.numOutputs; ++i) {
        RT_CHECK(rtMalloc(&outputDevs[i], bytes, RT_MEMORY_HBM, 0));
    }

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

    argPtrs.reserve(cfg.numInputs + cfg.numOutputs);
    for (void *ptr : inputDevs) {
        argPtrs.push_back(ptr);
    }
    for (void *ptr : outputDevs) {
        argPtrs.push_back(ptr);
    }

    RT_CHECK(rtKernelLaunch(stubFunc, 1, argPtrs.data(), static_cast<uint32_t>(argPtrs.size() * sizeof(void *)), nullptr, stream));
    RT_CHECK(rtStreamSynchronize(stream));

    for (size_t i = 0; i < cfg.numOutputs; ++i) {
        RT_CHECK(rtMemcpy(outputs[i].data(), bytes, outputDevs[i], bytes, RT_MEMCPY_DEVICE_TO_HOST));
    }

    std::printf("[INFO] native runtime sim finished for %s\n", kernelName.c_str());
    for (int i = 0; i < 8 && i < static_cast<int>(outputs[0].size()); ++i) {
        std::printf("  out[%d] = %.8f\n", i, outputs[0][static_cast<size_t>(i)]);
    }

    for (size_t i = 0; i < cfg.numInputs; ++i) {
        (void)WriteFloatFile("input" + std::to_string(i) + ".bin", inputs[i]);
    }
    for (size_t i = 0; i < cfg.numOutputs; ++i) {
        (void)WriteFloatFile("output" + std::to_string(i) + ".bin", outputs[i]);
    }

    if (BuildGolden(kernelName, inputs, golden)) {
        (void)WriteFloatFile("golden.bin", golden);
        const CompareStats stats = CompareOutputs(golden, outputs[0]);
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
    for (void *ptr : outputDevs) {
        if (ptr != nullptr) {
            (void)rtFree(ptr);
        }
    }
    for (void *ptr : inputDevs) {
        if (ptr != nullptr) {
            (void)rtFree(ptr);
        }
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
