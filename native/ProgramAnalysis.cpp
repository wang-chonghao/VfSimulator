// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramAnalysis.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

bool isDigits(const std::string &text) {
  if (text.empty())
    return false;
  return std::all_of(text.begin(), text.end(), [](unsigned char c) {
    return std::isdigit(c) != 0;
  });
}

} // namespace

ProgramNode ProgramNode::makeInst(ProgramInstNode value) {
  ProgramNode node;
  node.kind = Kind::Inst;
  node.inst = std::move(value);
  node.loop.reset();
  return node;
}

ProgramNode ProgramNode::makeLoop(ProgramLoopNode value) {
  ProgramNode node;
  node.kind = Kind::Loop;
  node.loop = std::make_shared<ProgramLoopNode>(std::move(value));
  return node;
}

ProgramAnalysis::ProgramAnalysis(ParamMap params) : params_(std::move(params)) {}

bool ProgramAnalysis::isVregName(const std::string &name) {
  if (name.size() < 2 || name[0] != 'v')
    return false;
  return isDigits(name.substr(1));
}

int64_t ProgramAnalysis::resolveBound(const std::string &bound) const {
  if (isDigits(bound))
    return std::stoll(bound);
  auto it = params_.find(bound);
  if (it != params_.end())
    return it->second;
  throw std::invalid_argument("Unsupported loop bound: " + bound);
}

int64_t ProgramAnalysis::resolveUnrollValue(const std::string &unroll) const {
  if (isDigits(unroll))
    return std::max<int64_t>(1, std::stoll(unroll));
  auto it = params_.find(unroll);
  if (it != params_.end())
    return std::max<int64_t>(1, it->second);
  return 1;
}

void ProgramAnalysis::collectVregsFromInst(
    const ProgramInstNode &inst,
    std::unordered_map<std::string, bool> &vregs) {
  for (const auto &name : inst.src) {
    if (isVregName(name))
      vregs.emplace(name, true);
  }
  for (const auto &name : inst.dst) {
    if (isVregName(name))
      vregs.emplace(name, true);
  }
}

void ProgramAnalysis::walkVregWarnings(const std::vector<ProgramNode> &nodes,
                                       const std::string &path,
                                       int64_t pregNum,
                                       std::vector<VregCapacityWarning> &warnings) const {
  int loopIndex = 0;
  for (const auto &node : nodes) {
    if (node.kind != ProgramNode::Kind::Loop || !node.loop)
      continue;

    ++loopIndex;
    const ProgramLoopNode &loop = *node.loop;
    const std::string loopPath = path + ".loop" + std::to_string(loopIndex);
    const int64_t unroll = resolveUnrollValue(loop.unroll);

    std::unordered_map<std::string, bool> vregs;
    std::vector<const ProgramNode *> stack;
    for (const auto &child : loop.body)
      stack.push_back(&child);

    while (!stack.empty()) {
      const ProgramNode *cur = stack.back();
      stack.pop_back();
      if (cur->kind == ProgramNode::Kind::Inst) {
        collectVregsFromInst(cur->inst, vregs);
        continue;
      }
      if (cur->loop) {
        for (const auto &child : cur->loop->body)
          stack.push_back(&child);
      }
    }

    const int64_t baseVregNamespace = static_cast<int64_t>(vregs.size());
    const int64_t expandedVregNamespace = baseVregNamespace * std::max<int64_t>(1, unroll);
    if (expandedVregNamespace > pregNum) {
      warnings.push_back(VregCapacityWarning{
          "vreg_namespace_overflow_risk",
          loopPath,
          pregNum,
          baseVregNamespace,
          unroll,
          expandedVregNamespace,
          "Unroll-expanded virtual-register namespace exceeds physical register count. Prediction may be low-confidence for this case.",
      });
    }

    walkVregWarnings(loop.body, loopPath, pregNum, warnings);
  }
}

std::vector<VregCapacityWarning>
ProgramAnalysis::collectVregCapacityWarnings(const std::vector<ProgramNode> &program,
                                             int64_t pregNum) const {
  std::vector<VregCapacityWarning> warnings;
  walkVregWarnings(program, "program", pregNum, warnings);
  return warnings;
}

std::vector<int64_t> ProgramAnalysis::inferNestedBoundsFromLoop(const ProgramLoopNode &loop) const {
  std::vector<int64_t> bounds;
  const ProgramLoopNode *cur = &loop;

  while (cur != nullptr && bounds.size() < 3) {
    bounds.push_back(resolveBound(cur->iters));
    const ProgramLoopNode *nextLoop = nullptr;
    for (const auto &node : cur->body) {
      if (node.kind == ProgramNode::Kind::Loop && node.loop) {
        nextLoop = node.loop.get();
        break;
      }
    }
    cur = nextLoop;
  }

  return bounds;
}

std::unordered_map<int, std::vector<int64_t>>
ProgramAnalysis::inferTopBlockLoopBounds(const std::vector<ProgramNode> &program) const {
  std::unordered_map<int, std::vector<int64_t>> result;
  int tbid = 0;

  for (const auto &node : program) {
    if (node.kind == ProgramNode::Kind::Loop && node.loop) {
      result.emplace(tbid++, inferNestedBoundsFromLoop(*node.loop));
    }
  }

  if (result.empty())
    result.emplace(0, std::vector<int64_t>{});

  return result;
}

} // namespace vfsim
