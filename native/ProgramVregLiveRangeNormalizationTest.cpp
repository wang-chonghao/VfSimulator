// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramVregLiveRangeNormalization.h"
#include "native/IFU.h"
#include "native/OOO.h"
#include "native/ParamDB.h"
#include "native/ProgramFlatten.h"

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

ProgramNode loop(std::vector<ProgramNode> body) {
  ProgramLoopNode value;
  value.iters = "32";
  value.unroll = "1";
  value.body = std::move(body);
  return ProgramNode::makeLoop(std::move(value));
}

ProgramNode loopWithIters(std::string iters, std::vector<ProgramNode> body) {
  ProgramLoopNode value;
  value.iters = std::move(iters);
  value.unroll = "1";
  value.body = std::move(body);
  return ProgramNode::makeLoop(std::move(value));
}

ValueInfo regValue(std::string id) {
  ValueInfo value;
  value.valueId = std::move(id);
  value.storage = ValueStorageKind::Register;
  value.dtype = "fp32";
  return value;
}

} // namespace

int main() {
  const std::vector<ProgramNode> program = {
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

  VfInfo vfInfo;
  vfInfo.values.emplace("v1", regValue("v1"));
  vfInfo.values.emplace("v0", regValue("v0"));
  vfInfo.body = {
      loop({
          inst("VADD", {"v1"}, {"s0", "s0"}),
          inst("VEXP", {"v0"}, {"v1"}),
          inst("VADD", {"v1"}, {"s1", "s1"}),
          inst("VADD", {"v4"}, {"v0", "v1"}),
      })};

  normalizeProgramVregLiveRanges(vfInfo);
  const auto &freshBody = vfInfo.body.front().loop->body;
  const std::string &fresh = freshBody[2].inst.dst[0];
  require(fresh != "v0" && fresh != "v1",
          "fresh register is allocated when all existing slots are live");
  auto valueIt = vfInfo.values.find(fresh);
  require(valueIt != vfInfo.values.end(),
          "fresh register value info is recorded");
  require(valueIt->second.storage == ValueStorageKind::Register,
          "fresh register uses explicit Register storage");
  require(valueIt->second.valueId == fresh,
          "fresh register value id matches the generated slot");

  VfInfo accumulatorInfo;
  accumulatorInfo.values.emplace("v5", regValue("v5"));
  accumulatorInfo.values.emplace("v6", regValue("v6"));
  accumulatorInfo.values.emplace("v7", regValue("v7"));
  accumulatorInfo.values.emplace("v9", regValue("v9"));
  accumulatorInfo.body = {loopWithIters(
      "4",
      {
          inst("VLDS", {"v6"}, {"mem0"}),
          inst("VEXP", {"v7"}, {"v6"}),
          inst("VADD", {"v5"}, {"v7", "v5"}),
          inst("VEXP", {"v9"}, {"v5"}),
      })};
  normalizeProgramVregLiveRanges(accumulatorInfo);
  const auto &accumulatorBody = accumulatorInfo.body.front().loop->body;
  require(accumulatorBody[2].inst.src[1] == accumulatorBody[2].inst.dst[0],
          "loop-carried accumulator must write its entry slot");
  require(accumulatorBody[3].inst.dst[0] != accumulatorBody[2].inst.dst[0],
          "temporary result must not reuse a loop-carried slot");

  ProgramAnalysis accumulatorAnalysis(accumulatorInfo.params,
                                      accumulatorInfo.values);
  ProgramFlatten accumulatorFlattener;
  const auto &accumulatorLinear =
      accumulatorFlattener.flatten(accumulatorInfo.body);
  IFU accumulatorIfu(
      accumulatorLinear,
      accumulatorInfo.params,
      nullptr,
      accumulatorAnalysis.inferTopBlockLoopBounds(accumulatorInfo.body),
      1,
      "fp32");
  const auto dynamicAccumulator = accumulatorIfu.take(16);
  ParamDB db(std::filesystem::path(VFSIM_SOURCE_ROOT));
  OoOCoreMainline accumulatorCore(db.uarch(), db, "fp32",
                                  accumulatorInfo.values);
  std::vector<int64_t> accumulatorIds;
  for (const DynamicInst &dynamicInst : dynamicAccumulator) {
    accumulatorCore.accept(dynamicInst);
    if (dynamicInst.op == "VADD")
      accumulatorIds.push_back(dynamicInst.instId);
  }
  require(accumulatorIds.size() == 4,
          "expected four dynamic accumulator instructions");
  for (size_t index = 1; index < accumulatorIds.size(); ++index) {
    require(
        accumulatorCore.getPregSrcForInst(accumulatorIds[index], 1) ==
            accumulatorCore.getPregDstForInst(accumulatorIds[index - 1], 0),
        "dynamic accumulator source must consume the previous iteration destination");
  }

  VfInfo exitAliasInfo;
  for (const std::string &name : {"v5", "v6", "v7", "v8"})
    exitAliasInfo.values.emplace(name, regValue(name));
  exitAliasInfo.body = {
      loopWithIters(
          "1",
          {
              inst("VLDS", {"v6"}, {"mem0"}),
              inst("VADD", {"v5"}, {"v7", "v5"}),
          }),
      inst("VSTS", {"mem1"}, {"v5"}),
  };
  normalizeProgramVregLiveRanges(exitAliasInfo);
  const std::string firstLoopExit =
      exitAliasInfo.body[0].loop->body[1].inst.dst[0];
  require(firstLoopExit != "v5",
          "single-iteration loop should expose its reused exit slot");
  require(exitAliasInfo.body[1].inst.src[0] == firstLoopExit,
          "following sibling must consume the loop exit alias");

  VfInfo redefinitionInfo;
  for (const std::string &name : {"v5", "v6", "v7", "v8"})
    redefinitionInfo.values.emplace(name, regValue(name));
  redefinitionInfo.body = {
      loopWithIters(
          "1",
          {
              inst("VLDS", {"v6"}, {"mem0"}),
              inst("VADD", {"v5"}, {"v7", "v5"}),
          }),
      loopWithIters(
          "4",
          {
              inst("VADD", {"v5"}, {"v8", "v8"}),
              inst("VSTS", {"mem1"}, {"v5"}),
          }),
  };
  normalizeProgramVregLiveRanges(redefinitionInfo);
  const auto &redefinitionBody = redefinitionInfo.body[1].loop->body;
  require(redefinitionBody[0].inst.dst[0] == "v5" &&
              redefinitionBody[1].inst.src[0] == "v5",
          "loop-local redefinition must kill a prior sibling alias");

  VfInfo zeroLoopInfo;
  zeroLoopInfo.params.emplace("ZERO", 0);
  for (const std::string &name : {"v5", "v6", "v7", "v8"})
    zeroLoopInfo.values.emplace(name, regValue(name));
  zeroLoopInfo.body = {
      loopWithIters(
          "1",
          {
              inst("VLDS", {"v6"}, {"mem0"}),
              inst("VADD", {"v5"}, {"v7", "v5"}),
          }),
      loopWithIters(
          "ZERO",
          {
              inst("VADD", {"v5"}, {"v8", "v8"}),
          }),
      inst("VSTS", {"mem1"}, {"v5"}),
  };
  normalizeProgramVregLiveRanges(zeroLoopInfo);
  const std::string zeroLoopEntry =
      zeroLoopInfo.body[0].loop->body[1].inst.dst[0];
  require(zeroLoopInfo.body[2].inst.src[0] == zeroLoopEntry,
          "zero-iteration loop must not kill an incoming alias");
  return 0;
}
