// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramCanonicalization.h"

#include <utility>

namespace vfsim {
namespace {

std::vector<std::string> renameLane(const std::vector<std::string> &values,
                                    int64_t lane) {
  std::vector<std::string> renamed;
  renamed.reserve(values.size());
  for (const auto &value : values)
    renamed.push_back(value + "_lane" + std::to_string(lane));
  return renamed;
}

std::vector<ProgramNode> expandBody(const std::vector<ProgramNode> &body,
                                    int64_t unroll) {
  std::vector<ProgramNode> expanded;
  expanded.reserve(body.size() * static_cast<size_t>(unroll));
  for (const auto &node : body) {
    for (int64_t lane = 0; lane < unroll; ++lane) {
      ProgramInstNode clone = node.inst;
      clone.src = renameLane(node.inst.src, lane);
      clone.dst = renameLane(node.inst.dst, lane);
      expanded.push_back(ProgramNode::makeInst(std::move(clone)));
    }
  }
  return expanded;
}

bool isInstructionOnlyBody(const std::vector<ProgramNode> &body) {
  for (const auto &node : body) {
    if (node.kind != ProgramNode::Kind::Inst)
      return false;
  }
  return true;
}

std::vector<ProgramNode> rewrite(const std::vector<ProgramNode> &nodes,
                                 const ProgramAnalysis &analysis,
                                 ProgramCanonicalizationStats &stats) {
  std::vector<ProgramNode> out;
  for (const auto &node : nodes) {
    if (node.kind != ProgramNode::Kind::Loop || !node.loop) {
      out.push_back(node);
      continue;
    }

    const ProgramLoopNode &loop = *node.loop;
    const int64_t iters = analysis.resolveBound(loop.iters);
    const int64_t unroll = analysis.resolveUnrollValue(loop.unroll);
    const bool shouldExpand = iters == 1 || (unroll > 1 && iters == unroll);
    if (isInstructionOnlyBody(loop.body) && shouldExpand) {
      auto expanded = expandBody(loop.body, unroll);
      ++stats.expandedLoops;
      stats.expandedInstructions += static_cast<int64_t>(expanded.size());
      out.insert(out.end(), std::make_move_iterator(expanded.begin()),
                 std::make_move_iterator(expanded.end()));
      continue;
    }

    ProgramLoopNode rewritten = loop;
    rewritten.body = rewrite(loop.body, analysis, stats);
    out.push_back(ProgramNode::makeLoop(std::move(rewritten)));
  }
  return out;
}

} // namespace

std::vector<ProgramNode> canonicalizeSingleSuperIterationLoops(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis::ParamMap &params, const ParamDB &db,
    const std::string &dtype, ProgramCanonicalizationStats *stats) {
  (void)db;
  (void)dtype;
  ProgramCanonicalizationStats localStats;
  ProgramAnalysis analysis(params);
  auto rewritten = rewrite(program, analysis, localStats);
  if (stats != nullptr)
    *stats = localStats;
  return rewritten;
}

} // namespace vfsim
