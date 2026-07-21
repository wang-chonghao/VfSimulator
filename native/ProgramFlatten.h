// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_PROGRAM_FLATTEN_H
#define VFSIM_NATIVE_PROGRAM_FLATTEN_H

#include "native/ProgramAnalysis.h"

#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct LinearProgramNode {
  enum class Kind { Inst, LoopBegin, LoopEnd };

  Kind kind = Kind::Inst;
  std::string type;
  std::string op;
  std::string form;
  int64_t pc = 0;
  int64_t depth = 0;
  std::vector<int64_t> loopStack;

  int64_t loopId = -1;
  int64_t iters = 1;
  std::string itersRaw;
  int64_t unroll = 1;
  std::string unrollRaw;
  std::string name;
  bool isInnermost = false;

  std::vector<std::string> src;
  std::vector<std::string> dst;
};

class ProgramFlatten {
public:
  explicit ProgramFlatten(ProgramAnalysis::ParamMap params = {});

  const std::vector<LinearProgramNode> &flatten(const std::vector<ProgramNode> &program);

  const std::vector<LinearProgramNode> &linear() const noexcept { return linear_; }

private:
  ProgramAnalysis analysis_;
  std::vector<LinearProgramNode> linear_;
  int64_t nextLoopId_ = 0;
  int64_t pc_ = 0;

  static bool containsAnyLoop(const std::vector<ProgramNode> &nodes);
  void visit(const std::vector<ProgramNode> &nodes, int64_t depth,
             const std::vector<int64_t> &loopStack);
  void emitInst(const ProgramInstNode &inst, int64_t depth,
                const std::vector<int64_t> &loopStack);
  void emitLoop(const ProgramLoopNode &loopNode, int64_t depth,
                const std::vector<int64_t> &loopStack);
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PROGRAM_FLATTEN_H
