// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/IFU.h"

#include <algorithm>
#include <utility>

namespace vfsim {

IFU::IFU(
    std::vector<DynamicInst> expandedInstructions,
    std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds,
    int64_t totalTopBlocks)
    : totalTopBlocks_(std::max<int64_t>(1, totalTopBlocks)),
      topBlockLoopBounds_(std::move(topBlockLoopBounds)) {
  for (auto &instruction : expandedInstructions)
    pending_.push_back(std::move(instruction));
}

std::optional<DynamicInst> IFU::nextInst() {
  if (pending_.empty())
    return std::nullopt;
  DynamicInst instruction = std::move(pending_.front());
  pending_.pop_front();
  return instruction;
}

std::vector<DynamicInst> IFU::take(int64_t n) {
  std::vector<DynamicInst> result;
  for (int64_t index = 0; index < std::max<int64_t>(0, n); ++index) {
    auto instruction = nextInst();
    if (!instruction)
      break;
    result.push_back(std::move(*instruction));
  }
  return result;
}

} // namespace vfsim
