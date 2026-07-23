// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/VfInfo.h"
#include "native/ParamDB.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vfsim;

ProgramNode inst(std::string op, std::vector<std::string> dst,
                 std::vector<std::string> src) {
  ProgramInstNode value;
  value.op = std::move(op);
  value.dst = std::move(dst);
  value.src = std::move(src);
  return ProgramNode::makeInst(std::move(value));
}

ValueInfo value(std::string id, ValueStorageKind storage, std::string dtype) {
  ValueInfo result;
  result.valueId = std::move(id);
  result.storage = storage;
  result.dtype = std::move(dtype);
  return result;
}

} // namespace

int main() {
  VfInfo vfInfo;
  vfInfo.params = {{"I", 16}, {"U", 1}};
  vfInfo.values.emplace("input0",
                        value("input0", ValueStorageKind::UB, "fp32"));
  vfInfo.values.emplace("input1",
                        value("input1", ValueStorageKind::UB, "fp32"));
  vfInfo.values.emplace("input2",
                        value("input2", ValueStorageKind::UB, "fp16"));
  vfInfo.values.emplace("output",
                        value("output", ValueStorageKind::UB, "fp16"));
  for (const auto &[id, dtype] :
       std::vector<std::pair<std::string, std::string>>{
           {"lhs", "fp32"}, {"rhs", "fp32"}, {"sum32", "fp32"},
           {"sum16", "fp16"}, {"extra16", "fp16"}, {"result", "fp16"}})
    vfInfo.values.emplace(id, value(id, ValueStorageKind::Register, dtype));

  ProgramLoopNode loop;
  loop.iters = "I";
  loop.unroll = "U";
  loop.body = {
      inst("VLDS", {"lhs"}, {"input0"}),
      inst("VLDS", {"rhs"}, {"input1"}),
      inst("VADD", {"sum32"}, {"lhs", "rhs"}),
      inst("VCVT_F32_TO_F16", {"sum16"}, {"sum32"}),
      inst("VLDS", {"extra16"}, {"input2"}),
      inst("VADD", {"result"}, {"sum16", "extra16"}),
      inst("VSTS", {"output"}, {"result"}),
  };
  vfInfo.body.push_back(ProgramNode::makeLoop(std::move(loop)));

  const ParamDB db(std::filesystem::path(VFSIM_SOURCE_ROOT));
  VfInfo canonical = vfInfo;
  canonicalizeVfInfo(canonical);
  const auto &body = canonical.body.front().loop->body;
  if (body[2].inst.form != "fp32" ||
      body[3].inst.form != "f32_to_f16" ||
      body[5].inst.form != "fp16")
    throw std::runtime_error("instruction forms were not inferred from ValueInfo");
  const SimulationResult result = runVfInfo(vfInfo, db);
  if (result.cyclesExecuted != 72 || result.vfEndCycle != 84)
    throw std::runtime_error("mixed-dtype VfInfo result changed: cycles=" +
                             std::to_string(result.cyclesExecuted) +
                             ", end=" + std::to_string(result.vfEndCycle));
  return 0;
}
