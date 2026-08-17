// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/CanonicalVfInfo.h"
#include "api/native/VfInfo.h"
#include "native/ParamDB.h"
#include "native/Json.h"
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
  const auto sharedFixture = json::parseFile(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "tests/fixtures/canonical_vf_info/v1_valid_loop.json");
  const auto &fixtureRoot = sharedFixture.asObject();
  if (fixtureRoot.at("schema_version").asInt() !=
          kCanonicalVfInfoSchemaVersion ||
      fixtureRoot.at("context").asArray().front().asObject().at("kind").asString() !=
          "loop" ||
      fixtureRoot.at("values").asObject().at("acc.0").asObject().at(
          "definition_id").asString() != "acc.0")
    throw std::runtime_error("shared CanonicalVfInfo v1 fixture changed shape");

  CanonicalVfInfo canonicalContract;
  CanonicalValue inputValue;
  inputValue.definitionId = "input.0";
  inputValue.logicalId = "input";
  inputValue.storage = CanonicalStorageKind::UB;
  inputValue.dtype = "fp32";
  canonicalContract.values.emplace(inputValue.definitionId, inputValue);
  CanonicalValue resultValue;
  resultValue.definitionId = "result.0";
  resultValue.logicalId = "result";
  resultValue.storage = CanonicalStorageKind::Register;
  resultValue.dtype = "fp32";
  resultValue.producerInstructionId = "inst.load";
  canonicalContract.values.emplace(resultValue.definitionId, resultValue);
  CanonicalInstruction canonicalLoad;
  canonicalLoad.instructionId = "inst.load";
  canonicalLoad.opcode = "VLDS";
  canonicalLoad.instructionClass = CanonicalInstructionClass::Load;
  canonicalLoad.form = "fp32";
  CanonicalOperand memoryInput;
  memoryInput.valueId = "input.0";
  memoryInput.role = CanonicalOperandRole::Memory;
  memoryInput.dtype = "fp32";
  CanonicalAffineExpression offset;
  offset.terms.push_back(CanonicalAffineTerm{"i", 1});
  memoryInput.memoryAccess = CanonicalMemoryAccess{
      "input.0", std::move(offset), CanonicalAccessKind::Read, 64, ""};
  canonicalLoad.inputs.push_back(memoryInput);
  canonicalLoad.outputs.push_back(
      CanonicalOperand{"result.0", CanonicalOperandRole::Destination,
                       "fp32", std::nullopt});
  CanonicalLoop canonicalLoop;
  canonicalLoop.loopId = "loop.0";
  canonicalLoop.induction.variableId = "i";
  canonicalLoop.count = int64_t{1};
  canonicalLoop.body.push_back(
      CanonicalNode::makeInstruction(std::move(canonicalLoad)));
  canonicalContract.context.push_back(CanonicalNode::makeLoop(std::move(canonicalLoop)));
  CanonicalMembar canonicalMembar;
  canonicalMembar.instructionId = "membar.0";
  canonicalMembar.barrier = "VST_VLD";
  canonicalContract.context.push_back(
      CanonicalNode::makeMembar(std::move(canonicalMembar)));
  if (!validateCanonicalVfInfo(canonicalContract).ok())
    throw std::runtime_error("valid native CanonicalVfInfo was rejected");

  CanonicalVfInfo invalidContract = canonicalContract;
  std::get<CanonicalMembar>(invalidContract.context.back().payload).barrier = "ALL";
  const auto invalidResult = validateCanonicalVfInfo(invalidContract);
  if (invalidResult.ok() || invalidResult.diagnostics.back().code !=
                                "unsupported_membar_type")
    throw std::runtime_error("invalid native Membar was not diagnosed");

  CanonicalVfInfo nullLoopContract;
  nullLoopContract.context.push_back(
      CanonicalNode{std::shared_ptr<CanonicalLoop>{}});
  const auto nullLoopResult = validateCanonicalVfInfo(nullLoopContract);
  if (nullLoopResult.ok() || nullLoopResult.diagnostics.front().code !=
                                 "invalid_loop_payload")
    throw std::runtime_error("null native loop payload was not diagnosed");

  CanonicalVfInfo hugeCountContract;
  CanonicalLoop hugeCountLoop;
  hugeCountLoop.loopId = "loop.huge";
  hugeCountLoop.induction.variableId = "j";
  hugeCountLoop.count = "999999999999999999999999999999";
  hugeCountContract.context.push_back(
      CanonicalNode::makeLoop(std::move(hugeCountLoop)));
  const auto hugeCountResult = validateCanonicalVfInfo(hugeCountContract);
  if (hugeCountResult.ok() || hugeCountResult.diagnostics.front().code !=
                                  "unresolved_parameter")
    throw std::runtime_error("oversized loop count was not diagnosed");

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
