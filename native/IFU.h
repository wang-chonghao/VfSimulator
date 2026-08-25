// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_IFU_H
#define VFSIM_NATIVE_IFU_H

#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vfsim {

struct DynamicInst {
  int64_t instId = 0;
  std::string type = "inst";
  std::string op;
  std::string form;
  std::string barrier;
  int64_t pc = -1;
  int64_t streamSeq = -1;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::string alignStateOperation;
  std::string alignStateId;
  std::vector<bool> srcValueRelease;
  std::vector<bool> dstValueKeep;
  std::string staticInstructionId;
  std::vector<std::pair<std::string, int64_t>> iterationPath;
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

// The production IFU consumes the dynamic stream emitted by Canonical
// lowering. Static ProgramNode expansion belongs to the migration library.
class IFU {
public:
  IFU(std::vector<DynamicInst> expandedInstructions,
      std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds,
      int64_t totalTopBlocks);

  bool done() const noexcept { return pending_.empty(); }
  std::optional<DynamicInst> nextInst();
  std::vector<DynamicInst> take(int64_t n);

  int totalTopBlocks() const noexcept { return totalTopBlocks_; }
  const std::unordered_map<int, std::vector<int64_t>> &
  topBlockLoopBounds() const noexcept {
    return topBlockLoopBounds_;
  }

private:
  std::deque<DynamicInst> pending_;
  int64_t totalTopBlocks_ = 1;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds_;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_IFU_H
