// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_IFU_H
#define VFSIM_NATIVE_IFU_H

#include "native/ParamDB.h"
#include "native/ProgramFlatten.h"

#include <deque>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace vfsim {

struct DynamicInst {
  int64_t instId = 0;
  std::string type = "inst";
  std::string op;
  std::string form;
  std::vector<std::string> src;
  std::vector<std::string> dst;

  std::vector<int64_t> loopStack;
  std::vector<int64_t> iterStack;
  int64_t loopDepth = 0;
  bool inLoop = false;

  int64_t unrollFactor = 1;
  int64_t lane = -1;
  int64_t unrollGroup = 0;
  int64_t origIterBase = 0;
  int64_t topBlockId = 0;
  bool isLastInTopBlock = false;
  std::vector<std::pair<std::string, std::vector<int64_t>>> blockKeyByLevel;
  std::vector<int64_t> blockEndLevels;
};

class IFU {
public:
  IFU(const std::vector<LinearProgramNode> &linearNodes,
      ProgramAnalysis::ParamMap params = {},
      const ParamDB *db = nullptr,
      std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds = {},
      int64_t totalTopBlocks = 0,
      std::string dtype = "fp32");

  bool done() const;
  std::optional<DynamicInst> nextInst();
  std::vector<DynamicInst> take(int64_t n);

  int totalTopBlocks() const noexcept { return totalTopBlocks_; }
  const std::unordered_map<int, std::vector<int64_t>> &topBlockLoopBounds() const noexcept {
    return topBlockLoopBounds_;
  }
  const std::vector<std::pair<int64_t, std::vector<int64_t>>> &vloopTrace() const noexcept {
    return vloopTrace_;
  }

private:
  struct LoopFrame {
    int64_t beginIdx = 0;
    int64_t endIdx = 0;
    int64_t loopId = 0;
    int64_t itersTotal = 0;
    int64_t iterNow = 0;
    bool isInnermost = false;
    int64_t unroll = 1;
    int64_t topBlockId = 0;
  };

  std::vector<LinearProgramNode> nodes_;
  ProgramAnalysis analysis_;
  const ParamDB *db_ = nullptr;
  std::string dtype_;

  std::unordered_map<int64_t, int64_t> beginToEnd_;
  std::unordered_map<int64_t, int64_t> beginLoopId_;
  std::unordered_map<int64_t, bool> isInnermostBegin_;
  std::unordered_map<int64_t, int64_t> beginTopBlockId_;
  std::unordered_map<int64_t, std::vector<LinearProgramNode>> loopBodyCache_;
  std::unordered_map<int64_t, std::optional<int64_t>> loopLastInstIdx_;
  std::unordered_map<int64_t, std::optional<int64_t>> topBlockLastInstIdx_;
  int64_t totalTopBlocks_ = 0;

  int64_t pc_ = 0;
  int64_t instId_ = 0;
  int64_t unrollGroup_ = 0;
  std::deque<DynamicInst> pending_;
  std::vector<LoopFrame> frames_;
  std::vector<std::pair<int64_t, std::vector<int64_t>>> vloopTrace_;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds_;

  void buildIndices();
  static bool containsAnyLoop(const std::vector<LinearProgramNode> &nodes);
  static bool isInst(const LinearProgramNode &node);
  static bool isLoopBegin(const LinearProgramNode &node);
  static bool isLoopEnd(const LinearProgramNode &node);

  std::pair<std::vector<int64_t>, std::vector<int64_t>> snapshot() const;
  int64_t currentTopBlockId() const;
  std::pair<std::string, std::vector<int64_t>>
  normalizeBlockKey(const std::pair<std::string, std::vector<int64_t>> &raw,
                    int64_t topBlockId) const;
  std::pair<std::string, std::vector<int64_t>>
  makeKey(int64_t topBlockId, const std::string &loopId,
          std::vector<int64_t> it) const;
  std::pair<std::vector<int64_t>, std::vector<int64_t>>
  currentBlockKeyPair(const DynamicInst &inst) const;

  std::vector<std::pair<std::string, std::vector<int64_t>>>
  buildBlockKeyByLevel(const std::vector<int64_t> &loopStack,
                       const std::vector<int64_t> &iterStack) const;
  std::vector<int64_t> calcBlockEndLevelsNormal() const;
  bool isLastInTopBlockNormal() const;
  DynamicInst emitNormalInst(const LinearProgramNode &node);
  void buildPendingUnrolled(LoopFrame &frame);

  void updateLastDispatch(const DynamicInst &inst, int64_t cycle);
  void triggerNextVloops(const DynamicInst &inst, int64_t cycle);
};

} // namespace vfsim

#endif // VFSIM_NATIVE_IFU_H
