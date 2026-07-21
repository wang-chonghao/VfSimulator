// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IFU.h"
#include "native/IDU.h"
#include "native/OOO.h"
#include "native/ParamDB.h"
#include "native/ProgramAnalysis.h"
#include "native/ProgramFlatten.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace vfsim;

void require(bool cond, const std::string &msg) {
  if (!cond)
    throw std::runtime_error(msg);
}

ProgramInstNode makeInst(std::string op, std::vector<std::string> dst,
                         std::vector<std::string> src) {
  ProgramInstNode inst;
  inst.op = std::move(op);
  inst.dst = std::move(dst);
  inst.src = std::move(src);
  return inst;
}

ProgramNode makeInstNode(const std::string &op, std::vector<std::string> dst,
                         std::vector<std::string> src) {
  return ProgramNode::makeInst(makeInst(op, std::move(dst), std::move(src)));
}

ProgramNode makeLoopNode(std::string iters, std::vector<ProgramNode> body) {
  ProgramLoopNode loop;
  loop.iters = std::move(iters);
  loop.unroll = "1";
  loop.name = "top_loop";
  loop.body = std::move(body);
  return ProgramNode::makeLoop(std::move(loop));
}

ProgramNode makeUnrolledLoopNode(std::string iters, std::string unroll,
                                 std::vector<ProgramNode> body) {
  ProgramLoopNode loop;
  loop.iters = std::move(iters);
  loop.unroll = std::move(unroll);
  loop.name = "unroll_order_loop";
  loop.body = std::move(body);
  return ProgramNode::makeLoop(std::move(loop));
}

std::vector<ProgramNode> buildTaddTmulProgram() {
  std::vector<ProgramNode> body;

  body.push_back(makeLoopNode(
      "1",
      {
          makeInstNode("VADDS", {"v2"}, {"v0", "v1"}),
      }));

  return body;
}

int countTopLevelLoops(const std::vector<ProgramNode> &program) {
  int count = 0;
  for (const auto &node : program)
    if (node.kind == ProgramNode::Kind::Loop)
      ++count;
  return count;
}

void verifyUnrollOrder(const ParamDB &db) {
  const std::vector<ProgramNode> program = {makeUnrolledLoopNode(
      "2", "2",
      {makeInstNode("VLDS", {"v0"}, {"mem0"}),
       makeInstNode("VADD", {"v1"}, {"v0", "v2"}),
       makeInstNode("VLDS", {"v3"}, {"mem1"}),
       makeInstNode("VSUB", {"v4"}, {"v1", "v3"}),
       makeInstNode("VSTS", {"mem2"}, {"v4"})})};

  ProgramFlatten flattener;
  const auto &linear = flattener.flatten(program);
  ProgramAnalysis analysis;
  IFU ifu(linear, {}, &db, analysis.inferTopBlockLoopBounds(program), 1,
          "fp32");
  const auto emitted = ifu.take(10);
  const std::vector<std::string> expected = {
      "VLDS", "VLDS", "VADD", "VADD", "VLDS",
      "VLDS", "VSUB", "VSUB", "VSTS", "VSTS"};
  require(emitted.size() == expected.size(),
          "unrolled IFU emitted an unexpected instruction count");
  for (size_t i = 0; i < expected.size(); ++i) {
    require(emitted[i].op == expected[i],
            "unrolled IFU must preserve static AABBCC order");
    require(emitted[i].lane == static_cast<int64_t>(i % 2),
            "unrolled IFU emitted an unexpected lane order");
  }
}

} // namespace

int main() {
  try {
    const std::filesystem::path root = std::filesystem::path(VFSIM_SOURCE_ROOT);
    ParamDB db(root);
    verifyUnrollOrder(db);

    const auto program = buildTaddTmulProgram();
    ProgramAnalysis analysis;
    const auto loopBounds = analysis.inferTopBlockLoopBounds(program);
    ProgramFlatten flattener;
    const auto &linear = flattener.flatten(program);

    require(!linear.empty(), "flattened program must not be empty");

    const int topBlocks = countTopLevelLoops(program);
    IFU ifu(linear, {}, &db, loopBounds, topBlocks, "fp32");
    IDU idu(db.uarch(), db, {}, {}, topBlocks, loopBounds, "fp32");
    OoOCoreMainline ooo(db.uarch(), db, "fp32");

    const auto result =
        runSimulation(ifu, idu, ooo, db.uarch(), {}, "", /*maxCycles=*/5000);

    require(result.vfEndCycle > 0 && result.vfEndCycle < 200,
            "expected a short native VfSimulator run, got vfEndCycle=" +
                std::to_string(result.vfEndCycle));
    require(result.cyclesExecuted > 0, "cyclesExecuted must be positive");

    std::cout << "vfsim_native_smoke_test passed: vfEndCycle=" << result.vfEndCycle
              << '\n';
    return 0;
  } catch (const std::exception &ex) {
    std::cerr << "vfsim_native_smoke_test failed: " << ex.what() << '\n';
    return 1;
  }
}
