// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. Please read the License for details.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IFU.h"

#include "native/ISATraits.h"

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

int64_t resolveInt(const std::string &text, const ProgramAnalysis::ParamMap &params,
                   int64_t defaultValue, int64_t minValue) {
  if (text.empty())
    return defaultValue;
  bool digits = true;
  size_t pos = 0;
  if (text[0] == '-') {
    pos = 1;
    digits = text.size() > 1;
  }
  for (; digits && pos < text.size(); ++pos) {
    if (!std::isdigit(static_cast<unsigned char>(text[pos])))
      digits = false;
  }
  if (digits) {
    try {
      return std::max<int64_t>(minValue, std::stoll(text));
    } catch (...) {
      return defaultValue;
    }
  }
  auto it = params.find(text);
  if (it != params.end())
    return std::max<int64_t>(minValue, it->second);
  return defaultValue;
}

} // namespace

IFU::IFU(const std::vector<LinearProgramNode> &linearNodes,
         ProgramAnalysis::ParamMap params,
         const ParamDB *db,
         std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds,
         int64_t totalTopBlocks,
         std::string dtype)
    : nodes_(linearNodes), analysis_(std::move(params)), dtype_(std::move(dtype)) {
  db_ = db;
  buildIndices();
  if (!topBlockLoopBounds.empty())
    topBlockLoopBounds_ = std::move(topBlockLoopBounds);
  else
    topBlockLoopBounds_ = analysis_.inferTopBlockLoopBounds({});
  if (totalTopBlocks > 0)
    totalTopBlocks_ = totalTopBlocks;
}

bool IFU::isInst(const LinearProgramNode &node) { return node.type == "inst"; }
bool IFU::isLoopBegin(const LinearProgramNode &node) { return node.type == "loop_begin"; }
bool IFU::isLoopEnd(const LinearProgramNode &node) { return node.type == "loop_end"; }

bool IFU::containsAnyLoop(const std::vector<LinearProgramNode> &nodes) {
  for (const auto &node : nodes) {
    if (node.type == "loop_begin")
      return true;
  }
  return false;
}

void IFU::buildIndices() {
  std::vector<int64_t> stack;
  for (int64_t i = 0; i < static_cast<int64_t>(nodes_.size()); ++i) {
    const auto &node = nodes_[static_cast<size_t>(i)];
    if (isLoopBegin(node)) {
      stack.push_back(i);
      beginLoopId_[i] = static_cast<int64_t>(beginLoopId_.size());
    } else if (isLoopEnd(node)) {
      if (stack.empty())
        throw std::runtime_error("Unmatched loop_end in linear program");
      const int64_t begin = stack.back();
      stack.pop_back();
      beginToEnd_[begin] = i;
    }
  }
  if (!stack.empty())
    throw std::runtime_error("Unmatched loop_begin in linear program");

  std::vector<int64_t> begins;
  begins.reserve(beginToEnd_.size());
  for (const auto &[b, _] : beginToEnd_)
    begins.push_back(b);
  std::sort(begins.begin(), begins.end());

  for (const auto b : begins) {
    const auto e = beginToEnd_.at(b);
    bool nested = false;
    for (const auto b2 : begins) {
      if (b2 == b)
        continue;
      if (b < b2 && b2 < e) {
        nested = true;
        break;
      }
    }
    isInnermostBegin_[b] = !nested;
  }

  int64_t topBid = 0;
  for (const auto b : begins) {
    bool enclosed = false;
    for (const auto b2 : begins) {
      if (b2 == b)
        continue;
      const auto e2 = beginToEnd_.at(b2);
      if (b2 < b && b < e2) {
        enclosed = true;
        break;
      }
    }
    if (!enclosed)
      beginTopBlockId_[b] = topBid++;
  }
  totalTopBlocks_ = topBid;

  for (const auto b : begins) {
    const auto e = beginToEnd_.at(b);
    std::vector<LinearProgramNode> body;
    for (int64_t i = b + 1; i < e; ++i) {
      if (isInst(nodes_[static_cast<size_t>(i)]))
        body.push_back(nodes_[static_cast<size_t>(i)]);
    }
    if (isInnermostBegin_.at(b))
      loopBodyCache_[b] = std::move(body);

    std::optional<int64_t> lastIdx;
    for (int64_t i = b + 1; i < e; ++i) {
      if (isInst(nodes_[static_cast<size_t>(i)]))
        lastIdx = i;
    }
    loopLastInstIdx_[b] = lastIdx;
  }

  for (const auto &[b, tbid] : beginTopBlockId_) {
    std::optional<int64_t> lastIdx;
    const auto e = beginToEnd_.at(b);
    for (int64_t i = b + 1; i < e; ++i) {
      if (isInst(nodes_[static_cast<size_t>(i)]))
        lastIdx = i;
    }
    topBlockLastInstIdx_[tbid] = lastIdx;
  }
}

bool IFU::done() const {
  return pc_ >= static_cast<int64_t>(nodes_.size()) && pending_.empty();
}

std::pair<std::vector<int64_t>, std::vector<int64_t>> IFU::snapshot() const {
  std::vector<int64_t> loopIds;
  std::vector<int64_t> iterNow;
  loopIds.reserve(frames_.size());
  iterNow.reserve(frames_.size());
  for (const auto &fr : frames_) {
    loopIds.push_back(fr.loopId);
    iterNow.push_back(fr.iterNow);
  }
  return {std::move(loopIds), std::move(iterNow)};
}

int64_t IFU::currentTopBlockId() const {
  if (!frames_.empty())
    return frames_.front().topBlockId;
  return 0;
}

std::pair<std::string, std::vector<int64_t>>
IFU::makeKey(int64_t topBlockId, const std::string &loopId,
             std::vector<int64_t> it) const {
  it.insert(it.begin(), topBlockId);
  return {loopId, std::move(it)};
}

std::pair<std::string, std::vector<int64_t>>
IFU::normalizeBlockKey(const std::pair<std::string, std::vector<int64_t>> &raw,
                       int64_t topBlockId) const {
  if (raw.second.empty())
    return raw;
  std::vector<int64_t> it = raw.second;
  if (!it.empty())
    it.insert(it.begin(), topBlockId);
  return {raw.first, std::move(it)};
}

std::vector<std::pair<std::string, std::vector<int64_t>>>
IFU::buildBlockKeyByLevel(const std::vector<int64_t> &loopStack,
                          const std::vector<int64_t> &iterStack) const {
  std::vector<std::pair<std::string, std::vector<int64_t>>> out;
  for (size_t lv = 0; lv < loopStack.size(); ++lv) {
    std::vector<int64_t> prefix(iterStack.begin(), iterStack.begin() + static_cast<std::ptrdiff_t>(lv));
    out.emplace_back("loop" + std::to_string(lv), std::move(prefix));
  }
  return out;
}

std::vector<int64_t> IFU::calcBlockEndLevelsNormal() const {
  if (frames_.empty())
    return {};

  const int64_t deepest = static_cast<int64_t>(frames_.size()) - 1;
  const LoopFrame &deepestFr = frames_.back();
  const auto it = loopLastInstIdx_.find(deepestFr.beginIdx);
  if (it == loopLastInstIdx_.end() || !it->second.has_value() || pc_ != it->second.value())
    return {};

  std::vector<int64_t> endLevels;
  for (int64_t lv = deepest; lv >= 0; --lv) {
    bool allFinal = true;
    for (int64_t kk = lv; kk <= deepest; ++kk) {
      const auto &fr = frames_[static_cast<size_t>(kk)];
      if (fr.iterNow != fr.itersTotal - 1) {
        allFinal = false;
        break;
      }
    }
    if (allFinal)
      endLevels.push_back(lv);
    else
      break;
  }
  return endLevels;
}

bool IFU::isLastInTopBlockNormal() const {
  if (frames_.empty())
    return false;
  const int64_t tbid = currentTopBlockId();
  const auto it = topBlockLastInstIdx_.find(tbid);
  if (it == topBlockLastInstIdx_.end() || !it->second.has_value() || pc_ != it->second.value())
    return false;
  for (const auto &fr : frames_) {
    if (fr.iterNow != fr.itersTotal - 1)
      return false;
  }
  return true;
}

DynamicInst IFU::emitNormalInst(const LinearProgramNode &node) {
  DynamicInst out;
  out.instId = instId_++;
  out.type = node.type;
  out.op = node.op;
  out.src = node.src;
  out.dst = node.dst;

  const auto [loopStack, iterStack] = snapshot();
  out.loopStack = loopStack;
  out.iterStack = iterStack;
  out.loopDepth = static_cast<int64_t>(loopStack.size());
  out.inLoop = !loopStack.empty();
  out.unrollFactor = 1;
  out.lane = -1;
  out.topBlockId = currentTopBlockId();
  out.isLastInTopBlock = isLastInTopBlockNormal();
  out.blockKeyByLevel = buildBlockKeyByLevel(loopStack, iterStack);
  out.blockEndLevels = calcBlockEndLevelsNormal();
  return out;
}

void IFU::buildPendingUnrolled(LoopFrame &frame) {
  const auto it = loopBodyCache_.find(frame.beginIdx);
  const std::vector<LinearProgramNode> empty;
  const std::vector<LinearProgramNode> &body = it == loopBodyCache_.end() ? empty : it->second;

  std::vector<LinearProgramNode> loads;
  std::vector<LinearProgramNode> alus;
  std::vector<LinearProgramNode> stores;
  for (const auto &n : body) {
    const auto cls = db_ ? getOpClass(*db_, n.op, dtype_) : OpClass::Compute;
    if (cls == OpClass::Load)
      loads.push_back(n);
    else if (cls == OpClass::Store)
      stores.push_back(n);
    else
      alus.push_back(n);
  }

  const auto [loopStack, iterStack] = snapshot();
  const int64_t U = frame.unroll;
  const int64_t origBase = frame.iterNow;
  const int64_t superIter = U > 0 ? origBase / U : origBase;
  const bool isLastSuperIter = (origBase + U >= frame.itersTotal);

  std::vector<DynamicInst> pending;
  pending.reserve((loads.size() + alus.size() + stores.size()) * static_cast<size_t>(std::max<int64_t>(1, U)));

  auto emitSeq = [&](const std::vector<LinearProgramNode> &seq) {
    for (const auto &ins : seq) {
      for (int64_t lane = 0; lane < U; ++lane) {
        DynamicInst inst;
        inst.instId = instId_++;
        inst.type = ins.type;
        inst.op = ins.op;
        inst.src = ins.src;
        inst.dst = ins.dst;
        inst.loopStack = loopStack;
        if (!iterStack.empty()) {
          inst.iterStack = iterStack;
          inst.iterStack.back() = superIter;
        }
        inst.loopDepth = static_cast<int64_t>(loopStack.size());
        inst.inLoop = true;
        inst.unrollFactor = U;
        inst.unrollGroup = unrollGroup_;
        inst.lane = lane;
        inst.origIterBase = origBase;
        for (auto &x : inst.src)
          x += "_lane" + std::to_string(lane);
        for (auto &x : inst.dst)
          x += "_lane" + std::to_string(lane);
        inst.topBlockId = frame.topBlockId;
        inst.isLastInTopBlock = false;
        inst.blockKeyByLevel = buildBlockKeyByLevel(loopStack, inst.iterStack);
        inst.blockEndLevels.clear();
        pending.push_back(std::move(inst));
      }
    }
  };

  emitSeq(loads);
  emitSeq(alus);
  emitSeq(stores);

  if (!pending.empty()) {
    std::vector<int64_t> endLevels;
    if (isLastSuperIter) {
      for (int64_t lv = static_cast<int64_t>(loopStack.size()) - 1; lv >= 0; --lv) {
        bool allFinal = true;
        for (int64_t kk = lv; kk < static_cast<int64_t>(frames_.size()); ++kk) {
          const auto &fr = frames_[static_cast<size_t>(kk)];
          const bool finalNow = (kk == static_cast<int64_t>(frames_.size()) - 1)
                                    ? isLastSuperIter
                                    : (fr.iterNow == fr.itersTotal - 1);
          if (!finalNow) {
            allFinal = false;
            break;
          }
        }
        if (allFinal)
          endLevels.push_back(lv);
        else
          break;
      }
    }
    pending.back().blockEndLevels = endLevels;

    if (isLastSuperIter) {
      bool topAllFinal = true;
      for (const auto &fr : frames_) {
        if (fr.beginIdx == frame.beginIdx)
          continue;
        if (fr.iterNow != fr.itersTotal - 1) {
          topAllFinal = false;
          break;
        }
      }
      pending.back().isLastInTopBlock = topAllFinal;
    }
  }

  ++unrollGroup_;
  for (auto &inst : pending)
    pending_.push_back(std::move(inst));
  frame.iterNow += U;
}

std::optional<DynamicInst> IFU::nextInst() {
  if (!pending_.empty()) {
    DynamicInst out = std::move(pending_.front());
    pending_.pop_front();
    return out;
  }

  while (pc_ < static_cast<int64_t>(nodes_.size())) {
    const auto &n = nodes_[static_cast<size_t>(pc_)];
    if (n.type == "loop_begin") {
      const int64_t iters = resolveInt(n.itersRaw, analysis_.params(), 1, 0);
      const int64_t end = beginToEnd_.at(pc_);
      const int64_t loopId = beginLoopId_.at(pc_);
      const bool isInnermost = isInnermostBegin_.at(pc_);
      const int64_t unroll = resolveInt(n.unrollRaw, analysis_.params(), 1, 1);
      if (iters <= 0) {
        pc_ = end + 1;
        continue;
      }

      const int64_t topBlockId = frames_.empty() ? beginTopBlockId_.at(pc_) : frames_.front().topBlockId;
      if (isInnermost && unroll > 1 && iters % unroll != 0)
        throw std::runtime_error("Invalid unroll: iters not divisible by unroll");

      frames_.push_back(LoopFrame{pc_, end, loopId, iters, 0, isInnermost,
                                  (isInnermost && unroll > 1) ? unroll : 1, topBlockId});
      pc_ = isInnermost && unroll > 1 ? end : pc_ + 1;
      continue;
    }

    if (n.type == "loop_end") {
      if (frames_.empty())
        throw std::runtime_error("loop_end encountered with empty runtime stack");
      LoopFrame &top = frames_.back();
      if (top.endIdx != pc_)
        throw std::runtime_error("loop_end mismatch with runtime top frame");

      if (top.isInnermost && top.unroll > 1) {
        if (top.iterNow < top.itersTotal) {
          buildPendingUnrolled(top);
          if (!pending_.empty()) {
            DynamicInst out = std::move(pending_.front());
            pending_.pop_front();
            return out;
          }
          return std::nullopt;
        }
        frames_.pop_back();
        ++pc_;
        continue;
      }

      if (top.iterNow + 1 < top.itersTotal) {
        ++top.iterNow;
        pc_ = top.beginIdx + 1;
        continue;
      }

      frames_.pop_back();
      ++pc_;
      continue;
    }

    if (n.type != "inst") {
      ++pc_;
      continue;
    }

    DynamicInst out = emitNormalInst(n);
    ++pc_;
    return out;
  }

  return std::nullopt;
}

std::vector<DynamicInst> IFU::take(int64_t n) {
  std::vector<DynamicInst> out;
  for (int64_t i = 0; i < std::max<int64_t>(0, n); ++i) {
    auto inst = nextInst();
    if (!inst.has_value())
      break;
    out.push_back(std::move(*inst));
  }
  return out;
}

} // namespace vfsim
