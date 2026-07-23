// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramVregLiveRangeNormalization.h"

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

ProgramNode loop(std::vector<ProgramNode> body) {
  ProgramLoopNode value;
  value.iters = "32";
  value.unroll = "1";
  value.body = std::move(body);
  return ProgramNode::makeLoop(std::move(value));
}

} // namespace

int main() {
  std::vector<ProgramNode> program = {
      loop({
          inst("VLDS", {"v0"}, {"mem0"}),
          inst("VADD", {"v1"}, {"v0", "v2"}),
          inst("VLDS", {"v3"}, {"mem1"}),
          inst("VSUB", {"v4"}, {"v1", "v3"}),
          inst("VSTS", {"mem2"}, {"v4"}),
      })};

  ProgramVregLiveRangeNormalizationStats stats;
  const auto normalized =
      normalizeProgramVregLiveRanges(program, {}, {}, &stats);
  const auto &body = normalized.front().loop->body;

  require(body[0].inst.dst[0] == "v0", "first load keeps v0");
  require(body[1].inst.src[0] == "v0", "vadd consumes load slot");
  require(body[1].inst.dst[0] == "v0",
          "single-source vadd reuses dead source slot");
  require(body[2].inst.dst[0] == "v3",
          "later load keeps its dst when no prior slot is dead");
  require(body[3].inst.src[0] == "v0" && body[3].inst.src[1] == "v3",
          "dependent sources are rewritten to normalized slots");
  require(body[3].inst.dst[0] == "v0",
          "sub result reuses a last-use source slot");
  require(body[4].inst.src[0] == "v0",
          "store consumes the normalized final value");
  require(stats.changedFields > 0, "normalization must report changes");
  return 0;
}
