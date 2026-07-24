// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramCanonicalization.h"

#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace vfsim;

void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

ProgramNode inst(std::string op, std::vector<std::string> dst,
                 std::vector<std::string> src) {
  return ProgramNode::makeInst(
      ProgramInstNode{std::move(op), std::move(src), std::move(dst)});
}

ProgramNode loop(std::string iters, std::string unroll,
                 std::vector<ProgramNode> body) {
  ProgramLoopNode value;
  value.iters = std::move(iters);
  value.unroll = std::move(unroll);
  value.body = std::move(body);
  return ProgramNode::makeLoop(std::move(value));
}

ProgramNode loopWithoutUnroll(std::string iters,
                              std::vector<ProgramNode> body) {
  ProgramLoopNode value;
  value.iters = std::move(iters);
  value.body = std::move(body);
  return ProgramNode::makeLoop(std::move(value));
}

} // namespace

int main() {
  const ParamDB db(std::filesystem::path(VFSIM_SOURCE_ROOT));
  const std::vector<ProgramNode> input = {
      loop("32", "1",
           {loop("C", "U",
                 {inst("VLDS", {"v0"}, {"mem0"}),
                  inst("VADD", {"v1"}, {"v0", "v2"}),
                  inst("VLDS", {"v3"}, {"mem1"}),
                  inst("VSUB", {"v4"}, {"v1", "v3"}),
                  inst("VSTS", {"mem1"}, {"v1"})})})};

  ProgramCanonicalizationStats stats;
  const auto result = canonicalizeSingleSuperIterationLoops(
      input, {{"C", 2}, {"U", 2}}, db, "fp32", &stats);

  require(stats.expandedLoops == 1, "expected one expanded loop");
  require(result.size() == 1 && result[0].loop,
          "outer loop must remain present");
  const auto &body = result[0].loop->body;
  require(body.size() == 10, "expected five instructions times two lanes");
  require(body[0].inst.op == "VLDS" && body[1].inst.op == "VADD" &&
              body[2].inst.op == "VLDS" && body[3].inst.op == "VSUB" &&
              body[4].inst.op == "VSTS" && body[5].inst.op == "VLDS" &&
              body[6].inst.op == "VADD" && body[7].inst.op == "VLDS" &&
              body[8].inst.op == "VSUB" && body[9].inst.op == "VSTS",
          "expanded order must be ABCABC");
  require(body[1].inst.src[0] == "v0_lane0" &&
              body[6].inst.src[0] == "v0_lane1",
          "lane-specific dependencies must be preserved");

  const auto partial = canonicalizeSingleSuperIterationLoops(
      input, {{"C", 4}, {"U", 2}}, db, "fp32");
  require(partial[0].loop->body.size() == 1 &&
              partial[0].loop->body[0].kind == ProgramNode::Kind::Loop,
          "outer loop must remain present");
  require(partial[0].loop->body[0].loop->iters == "2" &&
              partial[0].loop->body[0].loop->unroll == "1",
          "partial unroll must reduce trip count and consume unroll");
  require(partial[0].loop->body[0].loop->body.size() == 10,
          "partial unroll must duplicate the body by the factor");

  ProgramCanonicalizationStats oneIterationStats;
  const auto oneIteration = canonicalizeSingleSuperIterationLoops(
      {loopWithoutUnroll("1", {inst("VADD", {"v1"}, {"v0"})})}, {}, db,
      "fp32", &oneIterationStats);
  require(oneIterationStats.expandedLoops == 1,
          "single-iteration loop must be expanded without an unroll setting");
  require(oneIteration.size() == 1 &&
              oneIteration[0].kind == ProgramNode::Kind::Inst &&
              oneIteration[0].inst.src[0] == "v0_lane0",
          "single-iteration loop must become straight-line IR");
  return 0;
}
