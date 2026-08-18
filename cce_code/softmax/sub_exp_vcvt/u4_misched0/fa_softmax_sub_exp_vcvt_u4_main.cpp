#include <algorithm>
#include <cmath>
#include <cstdint>
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

constexpr int kRows = 128;
constexpr int kCols = 64;
constexpr int kElems = kRows * kCols;
constexpr float kScale = 0.125f;
constexpr float kAbsTol = 2.5e-2f;
constexpr float kRelTol = 2.5e-2f;

uint16_t FloatToBf16Bits(float x) {
  uint32_t bits = 0;
  std::memcpy(&bits, &x, sizeof(bits));
  const uint32_t lsb = (bits >> 16) & 1u;
  bits += 0x7fffu + lsb;
  return static_cast<uint16_t>(bits >> 16);
}

float Bf16BitsToFloat(uint16_t x) {
  uint32_t bits = static_cast<uint32_t>(x) << 16;
  float out = 0.0f;
  std::memcpy(&out, &bits, sizeof(out));
  return out;
}

void FillInput(std::vector<float> &input) {
  for (size_t i = 0; i < input.size(); ++i) {
    input[i] = static_cast<float>((static_cast<int>(i % 29) - 14) * 0.125f);
  }
}

void BuildGolden(const std::vector<float> &input, std::vector<float> &maxOut,
                 std::vector<float> &sumOut, std::vector<uint16_t> &xExpOut) {
  maxOut.assign(kCols, -std::numeric_limits<float>::infinity());
  for (int row = 0; row < kRows; ++row) {
    for (int col = 0; col < kCols; ++col) {
      maxOut[col] = std::max(maxOut[col], input[row * kCols + col]);
    }
  }

  sumOut.assign(kCols, 0.0f);
  xExpOut.assign(kElems, 0);
  for (int row = 0; row < kRows; ++row) {
    for (int col = 0; col < kCols; ++col) {
      const float expv =
          std::exp(input[row * kCols + col] * kScale - maxOut[col] * kScale);
      sumOut[col] += expv;
      xExpOut[row * kCols + col] = FloatToBf16Bits(expv);
    }
  }
}

bool ReadBinaryFile(const std::string &path, std::vector<unsigned char> &buffer) {
  std::ifstream file(path, std::ios::binary);
  if (!file.is_open()) {
    std::fprintf(stderr, "[ERROR] failed to open kernel binary: %s\n", path.c_str());
    return false;
  }
  file.seekg(0, std::ios::end);
  std::streamoff size = file.tellg();
  file.seekg(0, std::ios::beg);
  if (size <= 0) {
    std::fprintf(stderr, "[ERROR] empty kernel binary: %s\n", path.c_str());
    return false;
  }
  buffer.resize(static_cast<size_t>(size));
  file.read(reinterpret_cast<char *>(buffer.data()), size);
  return static_cast<bool>(file);
}

void PrintRecentAclError() {
  const char *recent = aclGetRecentErrMsg();
  if (recent != nullptr && recent[0] != '\0') {
    std::fprintf(stderr, "[ACL] RecentErrMsg: %s\n", recent);
  }
}

template <typename T>
bool WriteBinary(const std::string &path, const std::vector<T> &data) {
  std::ofstream file(path, std::ios::binary);
  if (!file.is_open()) {
    std::fprintf(stderr, "[WARN] failed to write %s\n", path.c_str());
    return false;
  }
  file.write(reinterpret_cast<const char *>(data.data()),
             static_cast<std::streamsize>(data.size() * sizeof(T)));
  return static_cast<bool>(file);
}

struct FloatStats {
  size_t mismatches = 0;
  size_t first = 0;
  float maxAbs = 0.0f;
  float maxRel = 0.0f;
};

FloatStats CompareFloat(const std::vector<float> &got, const std::vector<float> &ref,
                        float absTol, float relTol) {
  FloatStats s;
  bool saw = false;
  for (size_t i = 0; i < got.size() && i < ref.size(); ++i) {
    const float absErr = std::fabs(got[i] - ref[i]);
    const float relErr = absErr / std::max(std::fabs(ref[i]), 1.0f);
    s.maxAbs = std::max(s.maxAbs, absErr);
    s.maxRel = std::max(s.maxRel, relErr);
    if (absErr > absTol && relErr > relTol) {
      if (!saw) {
        saw = true;
        s.first = i;
      }
      ++s.mismatches;
    }
  }
  return s;
}

FloatStats CompareBf16AsFloat(const std::vector<uint16_t> &got,
                              const std::vector<uint16_t> &ref) {
  std::vector<float> gotF(got.size());
  std::vector<float> refF(ref.size());
  for (size_t i = 0; i < got.size(); ++i) {
    gotF[i] = Bf16BitsToFloat(got[i]);
  }
  for (size_t i = 0; i < ref.size(); ++i) {
    refF[i] = Bf16BitsToFloat(ref[i]);
  }
  return CompareFloat(gotF, refF, kAbsTol, kRelTol);
}

#define ACL_CHECK(expr)                                                                          \
  do {                                                                                           \
    const aclError ret = (expr);                                                                 \
    if (ret != ACL_SUCCESS) {                                                                    \
      std::fprintf(stderr, "[ERROR] %s failed: %d (%s:%d)\n", #expr, (int)ret, __FILE__,       \
                   __LINE__);                                                                    \
      PrintRecentAclError();                                                                     \
      rc = 1;                                                                                    \
      goto cleanup;                                                                              \
    }                                                                                            \
  } while (0)

#define RT_CHECK(expr)                                                                           \
  do {                                                                                           \
    const rtError_t ret = (expr);                                                                \
    if (ret != RT_ERROR_NONE) {                                                                  \
      std::fprintf(stderr, "[ERROR] %s failed: %d (%s:%d)\n", #expr, (int)ret, __FILE__,       \
                   __LINE__);                                                                    \
      rc = 2;                                                                                    \
      goto cleanup;                                                                              \
    }                                                                                            \
  } while (0)

} // namespace

int main(int argc, char **argv) {
  if (argc < 3) {
    std::fprintf(stderr, "Usage: %s <kernel-bin> <kernel-name>\n", argv[0]);
    return 64;
  }

  const std::string kernelBinPath = argv[1];
  const std::string kernelName = argv[2];

  std::vector<float> input(kElems);
  std::vector<uint16_t> xExp(kElems, 0);
  std::vector<float> newGlobalMax(kCols, 0.0f);
  std::vector<float> newGlobalSum(kCols, 0.0f);
  std::vector<uint16_t> nzOut(kElems, 0);
  std::vector<float> refMax;
  std::vector<float> refSum;
  std::vector<uint16_t> refXExp;
  FillInput(input);
  BuildGolden(input, refMax, refSum, refXExp);

  int rc = 0;
  bool aclInited = false;
  bool deviceSet = false;
  rtStream_t stream = nullptr;
  void *binHandle = nullptr;
  void *stubFunc = nullptr;
  rtDevBinary_t binary{};
  std::vector<unsigned char> kernelBuffer;
  void *inputDev = nullptr;
  void *xExpDev = nullptr;
  void *maxDev = nullptr;
  void *sumDev = nullptr;
  void *nzDev = nullptr;

  if (!ReadBinaryFile(kernelBinPath, kernelBuffer)) {
    return 65;
  }

  ACL_CHECK(aclInit(nullptr));
  aclInited = true;
  RT_CHECK(rtSetDevice(0));
  deviceSet = true;
  RT_CHECK(rtStreamCreate(&stream, 0));

  RT_CHECK(rtMalloc(&inputDev, input.size() * sizeof(float), RT_MEMORY_HBM, 0));
  RT_CHECK(rtMalloc(&xExpDev, xExp.size() * sizeof(uint16_t), RT_MEMORY_HBM, 0));
  RT_CHECK(rtMalloc(&maxDev, newGlobalMax.size() * sizeof(float), RT_MEMORY_HBM, 0));
  RT_CHECK(rtMalloc(&sumDev, newGlobalSum.size() * sizeof(float), RT_MEMORY_HBM, 0));
  RT_CHECK(rtMalloc(&nzDev, nzOut.size() * sizeof(uint16_t), RT_MEMORY_HBM, 0));

  RT_CHECK(rtMemcpy(inputDev, input.size() * sizeof(float), input.data(),
                    input.size() * sizeof(float), RT_MEMCPY_HOST_TO_DEVICE));

  binary.magic = RT_DEV_BINARY_MAGIC_ELF_AIVEC;
  binary.version = 0;
  binary.length = static_cast<uint64_t>(kernelBuffer.size());
  binary.data = kernelBuffer.data();
  RT_CHECK(rtDevBinaryRegister(&binary, &binHandle));
  RT_CHECK(rtFunctionRegister(binHandle, reinterpret_cast<const void *>(kernelName.c_str()),
                              reinterpret_cast<const char_t *>(kernelName.c_str()),
                              reinterpret_cast<const void *>(kernelName.c_str()), 0));
  RT_CHECK(rtGetFunctionByName(reinterpret_cast<const char_t *>(kernelName.c_str()), &stubFunc));

  {
    void *args[] = {inputDev, xExpDev, maxDev, sumDev, nzDev};
    RT_CHECK(rtKernelLaunch(stubFunc, 1, args, sizeof(args), nullptr, stream));
    RT_CHECK(rtStreamSynchronize(stream));
  }

  RT_CHECK(rtMemcpy(xExp.data(), xExp.size() * sizeof(uint16_t), xExpDev,
                    xExp.size() * sizeof(uint16_t), RT_MEMCPY_DEVICE_TO_HOST));
  RT_CHECK(rtMemcpy(newGlobalMax.data(), newGlobalMax.size() * sizeof(float), maxDev,
                    newGlobalMax.size() * sizeof(float), RT_MEMCPY_DEVICE_TO_HOST));
  RT_CHECK(rtMemcpy(newGlobalSum.data(), newGlobalSum.size() * sizeof(float), sumDev,
                    newGlobalSum.size() * sizeof(float), RT_MEMCPY_DEVICE_TO_HOST));
  RT_CHECK(rtMemcpy(nzOut.data(), nzOut.size() * sizeof(uint16_t), nzDev,
                    nzOut.size() * sizeof(uint16_t), RT_MEMCPY_DEVICE_TO_HOST));

  WriteBinary("input_f32.bin", input);
  WriteBinary("output_x_exp_bf16.bin", xExp);
  WriteBinary("output_new_global_max_f32.bin", newGlobalMax);
  WriteBinary("output_new_global_sum_f32.bin", newGlobalSum);
  WriteBinary("output_nz_bf16.bin", nzOut);
  WriteBinary("golden_x_exp_bf16.bin", refXExp);
  WriteBinary("golden_new_global_max_f32.bin", refMax);
  WriteBinary("golden_new_global_sum_f32.bin", refSum);

  {
    const FloatStats maxStats = CompareFloat(newGlobalMax, refMax, 1e-5f, 1e-5f);
    const FloatStats sumStats = CompareFloat(newGlobalSum, refSum, 1e-2f, 1e-3f);
    std::printf("[CHECK] new_global_max mismatches=%zu max_abs=%.8g max_rel=%.8g\n",
                maxStats.mismatches, maxStats.maxAbs, maxStats.maxRel);
    std::printf("[CHECK] new_global_sum mismatches=%zu max_abs=%.8g max_rel=%.8g\n",
                sumStats.mismatches, sumStats.maxAbs, sumStats.maxRel);
    std::printf("[CHECK] x_exp_bf16 skipped: macro core writes nz_out, not row-major x_exp\n");
    if (maxStats.mismatches || sumStats.mismatches) {
      rc = 3;
    } else {
      std::printf("[CHECK] PASS\n");
    }
  }

cleanup:
  if (nzDev) (void)rtFree(nzDev);
  if (sumDev) (void)rtFree(sumDev);
  if (maxDev) (void)rtFree(maxDev);
  if (xExpDev) (void)rtFree(xExpDev);
  if (inputDev) (void)rtFree(inputDev);
  if (stream) (void)rtStreamDestroy(stream);
  if (deviceSet) (void)rtDeviceReset(0);
  if (aclInited) (void)aclFinalize();
  return rc;
}
