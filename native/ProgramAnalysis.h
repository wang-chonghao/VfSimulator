// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_PROGRAM_ANALYSIS_H
#define VFSIM_NATIVE_PROGRAM_ANALYSIS_H

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct ProgramInstNode {
  std::string op;
  std::vector<std::string> src;
  std::vector<std::string> dst;
};

struct ProgramLoopNode;

struct ProgramNode {
  enum class Kind { Inst, Loop };

  Kind kind = Kind::Inst;
  ProgramInstNode inst;
  std::shared_ptr<ProgramLoopNode> loop;

  static ProgramNode makeInst(ProgramInstNode value);
  static ProgramNode makeLoop(ProgramLoopNode value);
};

struct ProgramLoopNode {
  std::string iters;
  std::string unroll = "1";
  std::string name;
  std::vector<ProgramNode> body;
};

struct VregCapacityWarning {
  std::string kind;
  std::string loopPath;
  int64_t pregNum = 0;
  int64_t baseVregNamespace = 0;
  int64_t unroll = 1;
  int64_t expandedVregNamespace = 0;
  std::string message;
};

class ProgramAnalysis {
public:
  using ParamMap = std::unordered_map<std::string, int64_t>;

  explicit ProgramAnalysis(ParamMap params = {});

  static bool isVregName(const std::string &name);

  int64_t resolveBound(const std::string &bound) const;
  int64_t resolveUnrollValue(const std::string &unroll) const;

  std::vector<VregCapacityWarning>
  collectVregCapacityWarnings(const std::vector<ProgramNode> &program,
                              int64_t pregNum) const;

  std::vector<int64_t> inferNestedBoundsFromLoop(const ProgramLoopNode &loop) const;

  std::unordered_map<int, std::vector<int64_t>>
  inferTopBlockLoopBounds(const std::vector<ProgramNode> &program) const;

  const ParamMap &params() const noexcept { return params_; }

private:
  ParamMap params_;

  static void collectVregsFromInst(const ProgramInstNode &inst,
                                   std::unordered_map<std::string, bool> &vregs);
  void walkVregWarnings(const std::vector<ProgramNode> &nodes,
                        const std::string &path,
                        int64_t pregNum,
                        std::vector<VregCapacityWarning> &warnings) const;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PROGRAM_ANALYSIS_H
