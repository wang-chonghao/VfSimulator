// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramFlatten.h"

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace vfsim {

ProgramFlatten::ProgramFlatten(ProgramAnalysis::ParamMap params)
    : analysis_(std::move(params)) {}

bool ProgramFlatten::containsAnyLoop(const std::vector<ProgramNode> &nodes) {
  for (const auto &node : nodes) {
    if (node.kind == ProgramNode::Kind::Loop)
      return true;
    if (node.kind == ProgramNode::Kind::Inst)
      continue;
    if (node.loop && containsAnyLoop(node.loop->body))
      return true;
  }
  return false;
}

const std::vector<LinearProgramNode> &
ProgramFlatten::flatten(const std::vector<ProgramNode> &program) {
  linear_.clear();
  nextLoopId_ = 0;
  pc_ = 0;
  visit(program, 0, {});
  return linear_;
}

void ProgramFlatten::visit(const std::vector<ProgramNode> &nodes, int64_t depth,
                           const std::vector<int64_t> &loopStack) {
  for (const auto &node : nodes) {
    if (node.kind == ProgramNode::Kind::Inst) {
      emitInst(node.inst, depth, loopStack);
      continue;
    }
    if (node.kind == ProgramNode::Kind::Loop && node.loop) {
      emitLoop(*node.loop, depth, loopStack);
      continue;
    }
    throw std::runtime_error("ProgramFlatten: invalid node");
  }
}

void ProgramFlatten::emitInst(const ProgramInstNode &inst, int64_t depth,
                              const std::vector<int64_t> &loopStack) {
  LinearProgramNode out;
  out.kind = LinearProgramNode::Kind::Inst;
  out.type = "inst";
  out.op = inst.op;
  out.pc = pc_++;
  out.depth = depth;
  out.loopStack = loopStack;
  out.src = inst.src;
  out.dst = inst.dst;
  linear_.push_back(std::move(out));
}

void ProgramFlatten::emitLoop(const ProgramLoopNode &loopNode, int64_t depth,
                              const std::vector<int64_t> &loopStack) {
  const int64_t loopId = nextLoopId_++;
  const int64_t iters = analysis_.resolveBound(loopNode.iters);
  const int64_t unroll = analysis_.resolveUnrollValue(loopNode.unroll);
  const bool isInnermost = !containsAnyLoop(loopNode.body);

  LinearProgramNode begin;
  begin.kind = LinearProgramNode::Kind::LoopBegin;
  begin.type = "loop_begin";
  begin.op = "VLOOPv2";
  begin.pc = pc_++;
  begin.depth = depth + 1;
  begin.loopStack = loopStack;
  begin.loopId = loopId;
  begin.iters = iters;
  begin.itersRaw = loopNode.iters;
  begin.unroll = isInnermost ? unroll : 1;
  begin.unrollRaw = loopNode.unroll;
  begin.name = loopNode.name;
  begin.isInnermost = isInnermost;
  linear_.push_back(std::move(begin));

  std::vector<int64_t> nextStack = loopStack;
  nextStack.push_back(loopId);
  visit(loopNode.body, depth + 1, nextStack);

  LinearProgramNode end;
  end.kind = LinearProgramNode::Kind::LoopEnd;
  end.type = "loop_end";
  end.op = "VLOOPv2";
  end.pc = pc_++;
  end.depth = depth + 1;
  end.loopStack = loopStack;
  end.loopId = loopId;
  end.name = loopNode.name;
  linear_.push_back(std::move(end));
}

} // namespace vfsim
