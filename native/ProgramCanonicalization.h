// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_PROGRAM_CANONICALIZATION_H
#define VFSIM_NATIVE_PROGRAM_CANONICALIZATION_H

#include "native/ParamDB.h"
#include "native/ProgramAnalysis.h"

#include <cstdint>
#include <string>
#include <vector>

namespace vfsim {

struct ProgramCanonicalizationStats {
  int64_t expandedLoops = 0;
  int64_t expandedInstructions = 0;
};

std::vector<ProgramNode> canonicalizeSingleSuperIterationLoops(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis::ParamMap &params,
    const ParamDB &db,
    const std::string &dtype = "fp32",
    ProgramCanonicalizationStats *stats = nullptr);

} // namespace vfsim

#endif // VFSIM_NATIVE_PROGRAM_CANONICALIZATION_H
