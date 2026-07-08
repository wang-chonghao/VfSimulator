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

std::vector<ProgramNode> buildTaddTmulProgram() {
  return {
      makeLoopNode(
          "16",
          {
              makeInstNode("VLDS", {"v1"}, {"mem1"}),
              makeInstNode("VLDS", {"v3"}, {"mem3"}),
              makeInstNode("VADD", {"v4"}, {"v1", "v3"}),
              makeInstNode("VLDS", {"v6"}, {"mem6"}),
              makeInstNode("VMUL", {"v7"}, {"v4", "v6"}),
              makeInstNode("VSTS", {"mem8"}, {"v7"}),
          }),
  };
}

int countTopLevelLoops(const std::vector<ProgramNode> &program) {
  int count = 0;
  for (const auto &node : program)
    if (node.kind == ProgramNode::Kind::Loop)
      ++count;
  return count;
}

} // namespace

int main() {
  try {
    const std::filesystem::path root = std::filesystem::path(VFSIM_SOURCE_ROOT);
    ParamDB db(root);

    const auto program = buildTaddTmulProgram();
    ProgramAnalysis analysis;
    const auto loopBounds = analysis.inferTopBlockLoopBounds(program);
    ProgramFlatten flattener;
    const auto &linear = flattener.flatten(program);

    const int topBlocks = countTopLevelLoops(program);
    IFU ifu(linear, {}, &db, loopBounds, topBlocks, "fp32");
    IDU idu(db.uarch(), db, {}, {}, topBlocks, loopBounds, "fp32");
    OoOCoreMainline ooo(db.uarch(), db, "fp32");

    const std::filesystem::path outDir = "/tmp/vfsim_tadd_tmul_native_debug";
    const auto result = runSimulation(
        ifu, idu, ooo, db.uarch(), {}, outDir.string(), /*maxCycles=*/5000);

    std::cout << "vfEndCycle=" << result.vfEndCycle << "\n";
    std::cout << "resultsDir=" << result.resultsDir << "\n";
    return 0;
  } catch (const std::exception &ex) {
    std::cerr << "vfsim_tadd_tmul_trace failed: " << ex.what() << '\n';
    return 1;
  }
}
