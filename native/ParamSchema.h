// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_PARAM_SCHEMA_H
#define VFSIM_NATIVE_PARAM_SCHEMA_H

#include <cstdint>
#include <string>
#include <unordered_map>

namespace vfsim {

struct InstConfig {
  int64_t pipelineStartupCost = 0;
  int64_t latency = 0;
  int64_t throughput = 0;
  int64_t pipelineDrainCost = 0;
  int64_t dataLoadCost = 0;
  int64_t dataStoreCost = 0;
  std::string exu;
  std::string dispatchExu;
  std::string opClass;
};

struct IsaDefaults {
  int64_t vfStartupCost = 0;
  int64_t vfDrainCost = 0;
};

struct UarchConfig {
  int64_t issuePorts = 0;
  int64_t loadPorts = 0;
  int64_t storePorts = 0;
  int64_t iduWindowWidth = 0;
  int64_t iduIssueWidth = 0;
  int64_t ldqWidth = 0;
  int64_t vregNum = 0;
  bool enableIsuQueueModel = false;
  int64_t shqDepth = 0;
  int64_t exqDepth = 0;
  bool admitBlockedToExq = false;
  bool enableShqCreditModel = false;
  int64_t shqReleaseDelay = 0;
  bool enableCreditVisibilityDelay = false;
  int64_t iduVisiblePregDelay = 0;
  int64_t iduVisibleShqDelay = 0;
  bool globalShqPregGate = false;
  bool useExplicitIduCreditBank = false;
  int64_t iduToOooDelay = 0;
  int64_t vloopToDispatchDelay = 0;
  int64_t iduDispatchStartAdvance = 0;
  int64_t initialTopBlockVloopStartCycle = 0;
  int64_t nestedVloopInitialStartGap = 0;
  int64_t loop1MinFeedbackGap = 0;
  int64_t innermostIterDispatchStride = 0;
  int64_t consumerReleaseStartOffset = 0;
  int64_t loadDoneLatency = 0;
  int64_t oooToShqDelay = 0;
  int64_t oooToLsqDelay = 0;
  int64_t exqRecvDelay = 0;
  int64_t shqToExqPortPerCycle = 0;
  int64_t computeInflightCap = 0;
  int64_t exqIssueInflightCapPerPort = 0;
  bool exqCapacityCountsInflight = false;
  std::string memBarMode;
  bool enforceSameCycleSrcHazard = false;
  bool enableCrossFuIi = false;
};

using DTypeName = std::string;
using OpName = std::string;

struct ParamBundle {
  IsaDefaults isaDefaults;
  std::unordered_map<OpName, std::unordered_map<DTypeName, InstConfig>> isa;
  std::unordered_map<DTypeName, std::unordered_map<OpName, std::unordered_map<OpName, int64_t>>> forwarding;
  std::unordered_map<DTypeName, std::unordered_map<OpName, std::unordered_map<OpName, int64_t>>> initiationInterval;
  std::unordered_map<std::string, std::unordered_map<std::string, int64_t>> forwardingByForm;
  std::unordered_map<std::string, std::unordered_map<std::string, int64_t>> initiationIntervalByForm;
  UarchConfig uarch;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PARAM_SCHEMA_H
