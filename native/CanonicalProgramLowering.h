// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_CANONICAL_PROGRAM_LOWERING_H
#define VFSIM_NATIVE_CANONICAL_PROGRAM_LOWERING_H

#include "api/native/CanonicalVfInfo.h"
#include "api/native/RuntimeValue.h"
#include "api/native/RuntimeTypes.h"
#include "native/IFU.h"
#include "native/ParamDB.h"

#include <unordered_map>
#include <unordered_set>
#include <set>
#include <vector>

namespace vfsim {

struct CanonicalRuntimeProgram {
  std::vector<DynamicInst> instructions;
  std::unordered_map<std::string, ValueInfo> values;
  RuntimeParamMap params;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds;
  std::unordered_set<int64_t> emptyTopBlocks;
  int64_t totalTopBlocks = 1;
  std::string dtype = "fp32";
};

CanonicalRuntimeProgram lowerCanonicalProgram(const CanonicalVfInfo &vfInfo,
                                               const ParamDB *db = nullptr);
UarchConfig resolveCanonicalUarch(const CanonicalVfInfo &vfInfo,
                                  const UarchConfig &defaults);
std::set<std::string> cppResolvedUarchOverrideFields();

} // namespace vfsim

#endif // VFSIM_NATIVE_CANONICAL_PROGRAM_LOWERING_H
