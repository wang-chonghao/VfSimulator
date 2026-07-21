// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IDU.h"

#include "native/ISATraits.h"

#include <algorithm>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

std::string joinInts(const std::vector<int64_t> &values) {
  std::ostringstream oss;
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ',';
    oss << values[i];
  }
  return oss.str();
}

} // namespace

IDU::IDU(const UarchConfig &uarch,
         const ParamDB &db,
         ProgramAnalysis::ParamMap params,
         std::vector<int64_t> loopBounds,
         int64_t totalTopBlocks,
         std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds,
         std::string dtype)
    : db_(db), dtype_(std::move(dtype)), analysis_(std::move(params)),
      loopBounds_(std::move(loopBounds)), totalTopBlocks_(totalTopBlocks),
      topBlockLoopBounds_(std::move(topBlockLoopBounds)) {
  windowWidth_ = uarch.iduWindowWidth;
  issueWidth_ = uarch.iduIssueWidth;
  theoreticalLimitMode_ = false;
  theoreticalLimitVloopOnly_ = false;
  vfStartupCost_ = db_.isaDefaults().vfStartupCost;
  iduDispatchStartAdvance_ = uarch.iduDispatchStartAdvance;
  vloopToDispatchDelay_ = uarch.vloopToDispatchDelay;
  initialTopBlockVloopStartCycle_ = uarch.initialTopBlockVloopStartCycle;
  nestedVloopInitialStartGap_ = uarch.nestedVloopInitialStartGap;
  loop1MinFeedbackGap_ = uarch.loop1MinFeedbackGap;
  innermostIterDispatchStride_ = uarch.innermostIterDispatchStride;
  globalShqPregGate_ = uarch.globalShqPregGate;
  initVloopStarts();
}

bool IDU::canAccept() const {
  if (theoreticalLimitMode_)
    return true;
  return static_cast<int64_t>(window_.size()) < windowWidth_;
}

void IDU::accept(const DynamicInst &inst) {
  if (theoreticalLimitMode_ || static_cast<int64_t>(window_.size()) < windowWidth_)
    window_.push_back(inst);
}

void IDU::setTopBlockVloop(int64_t topBlockId, int64_t startCycle) {
  if (topBlockVloopStart_.count(topBlockId))
    return;
  topBlockVloopStart_[topBlockId] = startCycle;
  topBlockBodyOpenTime_[topBlockId] = startCycle + vloopToDispatchDelay_;
  vloopTrace_.push_back(VloopTraceRecord{topBlockId, "top_block", {}, startCycle});
}

void IDU::initTopBlockNestedStarts(int64_t topBlockId, int64_t topVloopStart) {
  const auto it = topBlockLoopBounds_.find(static_cast<int>(topBlockId));
  const std::vector<int64_t> &bounds = it == topBlockLoopBounds_.end() ? loopBounds_ : it->second;
  const int64_t depth = static_cast<int64_t>(bounds.size());
  if (depth <= 0)
    return;

  setTopBlockVloop(topBlockId, topVloopStart);
  if (depth >= 1) {
    const std::string key0 = makeKey(topBlockId, "loop0", {});
    vloopStart_[key0] = topVloopStart;
    bodyOpenTime_[key0] = topVloopStart + vloopToDispatchDelay_;
  }
  if (depth >= 2 && bounds[0] > 0) {
    const std::string key1 = makeKey(topBlockId, "loop1", {0});
    vloopStart_[key1] = topVloopStart + nestedVloopInitialStartGap_;
    bodyOpenTime_[key1] = vloopStart_[key1] + vloopToDispatchDelay_;
  }
  if (depth >= 3 && bounds[0] > 0 && bounds[1] > 0) {
    const std::string key2 = makeKey(topBlockId, "loop2", {0, 0});
    vloopStart_[key2] = topVloopStart + 2 * nestedVloopInitialStartGap_;
    bodyOpenTime_[key2] = vloopStart_[key2] + vloopToDispatchDelay_;
  }
}

void IDU::initVloopStarts() {
  if (totalTopBlocks_ <= 0)
    return;
  setTopBlockVloop(0, initialTopBlockVloopStartCycle_);
  initTopBlockNestedStarts(0, initialTopBlockVloopStartCycle_);
}

std::string IDU::makeKey(int64_t topBlockId, const std::string &loopId,
                         const std::vector<int64_t> &iters) const {
  return std::to_string(topBlockId) + "|" + loopId + "|" + joinInts(iters);
}

std::optional<std::string>
IDU::normalizeBlockKey(const std::pair<std::string, std::vector<int64_t>> &raw,
                       int64_t topBlockId) const {
  if (raw.first.empty())
    return std::nullopt;
  return makeKey(topBlockId, raw.first, raw.second);
}

std::optional<std::string> IDU::currentInnerBlockKey(const DynamicInst &inst) const {
  const int64_t topBlockId = inst.topBlockId;
  const auto &bk = inst.blockKeyByLevel;
  if (!bk.empty()) {
    return normalizeBlockKey(bk.back(), topBlockId);
  }

  const auto &iterStack = inst.iterStack;
  const int64_t depth = std::min<int64_t>(static_cast<int64_t>(loopBounds_.size()),
                                          static_cast<int64_t>(inst.loopDepth));
  if (depth <= 0)
    return std::nullopt;
  if (depth == 1)
    return makeKey(topBlockId, "loop0", {});
  if (depth == 2 && iterStack.size() >= 1)
    return makeKey(topBlockId, "loop1", {iterStack[0]});
  if (depth >= 3 && iterStack.size() >= 2)
    return makeKey(topBlockId, "loop2", {iterStack[0], iterStack[1]});
  return std::nullopt;
}

bool IDU::isLastInstOfTopBlock(const DynamicInst &inst) const {
  if (inst.blockEndLevels.empty())
    return false;
  return inst.isLastInTopBlock;
}

void IDU::updateLastDispatch(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto &bk = inst.blockKeyByLevel;
  if (!bk.empty()) {
    for (const auto &raw : bk) {
      if (auto key = normalizeBlockKey(raw, topBlockId))
        lastDispatchTime_[*key] = cycle;
    }
    return;
  }

  const auto &iterStack = inst.iterStack;
  const int64_t depth = static_cast<int64_t>(inst.loopDepth);
  if (depth == 1) {
    lastDispatchTime_[makeKey(topBlockId, "loop0", {})] = cycle;
  } else if (depth == 2 && iterStack.size() >= 1) {
    lastDispatchTime_[makeKey(topBlockId, "loop1", {iterStack[0]})] = cycle;
  } else if (depth >= 3 && iterStack.size() >= 2) {
    lastDispatchTime_[makeKey(topBlockId, "loop2", {iterStack[0], iterStack[1]})] = cycle;
    lastDispatchTime_[makeKey(topBlockId, "loop1", {iterStack[0]})] = cycle;
    lastDispatchTime_[makeKey(topBlockId, "loop0", {})] = cycle;
  }
}

void IDU::triggerNextVloops(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto boundsIt = topBlockLoopBounds_.find(static_cast<int>(topBlockId));
  const std::vector<int64_t> &bounds = boundsIt == topBlockLoopBounds_.end() ? loopBounds_ : boundsIt->second;
  const int64_t depth = static_cast<int64_t>(bounds.size());

  if (inst.isLastInTopBlock) {
    const int64_t nextTop = topBlockId + 1;
    if (nextTop < totalTopBlocks_ && !topBlockVloopStart_.count(nextTop)) {
      setTopBlockVloop(nextTop, cycle);
      initTopBlockNestedStarts(nextTop, cycle);
    }
  }

  if (depth <= 0 || inst.blockEndLevels.empty())
    return;

  const auto &iterStack = inst.iterStack;
  if (depth == 1)
    return;

  if (depth == 2) {
    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 1) != inst.blockEndLevels.end() &&
        iterStack.size() >= 1) {
      const int64_t i = iterStack[0];
      const int64_t I = bounds[0];
      const std::string curKey = makeKey(topBlockId, "loop1", {i});
      const int64_t endCy = lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (i + 1 < I) {
        const int64_t prevStart = vloopStart_.count(curKey) ? vloopStart_[curKey] : endCy;
        const int64_t nextStart = std::max<int64_t>(endCy, prevStart + loop1MinFeedbackGap_);
        const std::string nextKey = makeKey(topBlockId, "loop1", {i + 1});
        vloopStart_[nextKey] = nextStart;
        bodyOpenTime_[nextKey] = nextStart + vloopToDispatchDelay_;
        vloopTrace_.push_back(VloopTraceRecord{topBlockId, "loop1", {i + 1}, nextStart});
      }
    }
    return;
  }

  if (depth == 3) {
    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 2) != inst.blockEndLevels.end() &&
        iterStack.size() >= 2) {
      const int64_t i = iterStack[0];
      const int64_t j = iterStack[1];
      const int64_t M = bounds[1];
      const std::string curKey = makeKey(topBlockId, "loop2", {i, j});
      const int64_t endCy = lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (j + 1 < M) {
        const std::string nextKey = makeKey(topBlockId, "loop2", {i, j + 1});
        vloopStart_[nextKey] = endCy;
        bodyOpenTime_[nextKey] = endCy + vloopToDispatchDelay_;
        vloopTrace_.push_back(VloopTraceRecord{topBlockId, "loop2", {i, j + 1}, endCy});
      }
    }

    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 1) != inst.blockEndLevels.end() &&
        iterStack.size() >= 1) {
      const int64_t i = iterStack[0];
      const int64_t K = bounds[0];
      const int64_t M = bounds[1];
      const std::string curKey = makeKey(topBlockId, "loop1", {i});
      const int64_t endCy = lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (i + 1 < K) {
        const int64_t prevStart = vloopStart_.count(curKey) ? vloopStart_[curKey] : endCy;
        const int64_t nextLoop1Start = std::max<int64_t>(endCy, prevStart + loop1MinFeedbackGap_);
        vloopStart_[makeKey(topBlockId, "loop1", {i + 1})] = nextLoop1Start;
        bodyOpenTime_[makeKey(topBlockId, "loop1", {i + 1})] = nextLoop1Start + vloopToDispatchDelay_;
        vloopTrace_.push_back(VloopTraceRecord{topBlockId, "loop1", {i + 1}, nextLoop1Start});
        if (M > 0) {
          const int64_t childStart = nextLoop1Start + nestedVloopInitialStartGap_;
          vloopStart_[makeKey(topBlockId, "loop2", {i + 1, 0})] = childStart;
          bodyOpenTime_[makeKey(topBlockId, "loop2", {i + 1, 0})] = childStart + vloopToDispatchDelay_;
          vloopTrace_.push_back(VloopTraceRecord{topBlockId, "loop2", {i + 1, 0}, childStart});
        }
      }
    }
  }
}

std::vector<DynamicInst> IDU::dispatch(int64_t cycle, const IDUDispatchBudget &budget) {
  if (window_.empty())
    return {};

  const int64_t dispatchStartGate = std::max<int64_t>(0, vfStartupCost_ - iduDispatchStartAdvance_);
  if (cycle < dispatchStartGate)
    return {};

  int64_t credits = budget.theoreticalLimitMode ? (1LL << 60) : budget.freePreg;
  int64_t shqQueueFree = budget.theoreticalLimitMode ? (1LL << 60) : budget.freeShqQueue;
  int64_t lsqFree = budget.theoreticalLimitMode ? (1LL << 60) : budget.freeLsq;
  int64_t shqFree = budget.theoreticalLimitMode ? (1LL << 60) : budget.freeShq;
  const int64_t issueBudget = budget.theoreticalLimitMode ? static_cast<int64_t>(window_.size())
                                                          : budget.issueBudget;

  std::vector<DynamicInst> dispatched;
  dispatched.reserve(static_cast<size_t>(std::max<int64_t>(0, issueBudget)));

  if (shqQueueFree <= 0 && lsqFree <= 0)
    return {};
  if (globalShqPregGate_ && (credits <= 0 || shqFree <= 0))
    return {};

  for (const auto &inst : window_) {
    if (static_cast<int64_t>(dispatched.size()) >= issueBudget)
      break;

    const int64_t topBlockId = inst.topBlockId;
    const auto topOpenIt = topBlockBodyOpenTime_.find(topBlockId);
    if (topOpenIt == topBlockBodyOpenTime_.end() || cycle < topOpenIt->second)
      break;

    if (!budget.theoreticalLimitVloopOnly) {
      const auto innerKey = currentInnerBlockKey(inst);
      if (innerKey) {
        const auto openIt = bodyOpenTime_.find(*innerKey);
        if (openIt == bodyOpenTime_.end() || cycle < openIt->second)
          break;
      }
    }

    const int64_t iterId = inst.iterStack.empty() ? 0 : inst.iterStack.back();
    if (!budget.theoreticalLimitMode && !budget.theoreticalLimitVloopOnly) {
      const auto innerKey = currentInnerBlockKey(inst);
      if (innerKey) {
        if (iterId == 0 && !blockBaseCycle_.count(*innerKey))
          blockBaseCycle_[*innerKey] = cycle;
        const auto baseIt = blockBaseCycle_.find(*innerKey);
        if (baseIt == blockBaseCycle_.end())
          break;
        if (cycle < baseIt->second + iterId * innermostIterDispatchStride_)
          break;
      }
    }

    const std::string &form = inst.form.empty() ? dtype_ : inst.form;
    const bool isLoad = isLoadOp(db_, inst.op, form);
    const bool isStore = isStoreOp(db_, inst.op, form);
    if (isLoad) {
      if (lsqFree <= 0)
        break;
    } else if (isStore) {
      if (lsqFree <= 0 || shqFree <= 0)
        break;
    } else {
      if (shqQueueFree <= 0 || shqFree <= 0)
        break;
    }

    int64_t dstCount = 0;
    for (const auto &d : inst.dst) {
      if (!d.empty() && (d[0] == 'v' || d[0] == 'V'))
        ++dstCount;
    }
    if (credits < dstCount)
      break;

    dispatched.push_back(inst);
    credits -= dstCount;
    if (usesLsq(db_, inst.op, form)) {
      --lsqFree;
      if (usesSharedShqCredit(db_, inst.op, form))
        --shqFree;
    } else if (usesShqQueue(db_, inst.op, form)) {
      --shqQueueFree;
      --shqFree;
    }
  }

  for (const auto &inst : dispatched) {
    window_.pop_front();
    dispatchLog_.push_back(IDUDispatchRecord{
        cycle,
        inst.instId,
        inst.op,
        inst.dst,
        inst.src,
        inst.topBlockId,
        credits,
        shqQueueFree,
        lsqFree,
        shqFree,
    });
    updateLastDispatch(inst, cycle);
    triggerNextVloops(inst, cycle);
  }

  return dispatched;
}

} // namespace vfsim
