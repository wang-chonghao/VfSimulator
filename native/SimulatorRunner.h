// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_SIMULATOR_RUNNER_H
#define VFSIM_NATIVE_SIMULATOR_RUNNER_H

#include "native/IDU.h"
#include "native/OOO.h"

#include <string>

namespace vfsim {

struct SimulationResult {
  int64_t cyclesExecuted = 0;
  int64_t vfEndCycle = 0;
  std::string resultsDir;
};

SimulationResult runSimulation(IFU &ifu,
                               IDU &idu,
                               OoOCoreMainline &ooo,
                               const UarchConfig &uarch,
                               const ProgramAnalysis::ParamMap &params,
                               const std::string &resultsDir,
                               int64_t maxCycles = 1000000);

} // namespace vfsim

#endif // VFSIM_NATIVE_SIMULATOR_RUNNER_H
