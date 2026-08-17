// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/CanonicalVfInfo.h"
#include "api/native/VfInfo.h"
#include "native/ParamDB.h"
#include "native/Json.h"
#include "native/CanonicalVfInfoFixtureDecoder.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <limits>
#include <stdexcept>
#include <string>
#include <type_traits>
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
  const auto sharedFixtureJson = json::parseFile(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "tests/fixtures/canonical_vf_info/v1_valid_loop.json");
  CanonicalVfInfo canonicalContract =
      decodeCanonicalVfInfoFixture(sharedFixtureJson);
  if (!validateCanonicalVfInfo(canonicalContract).ok())
    throw std::runtime_error("shared valid CanonicalVfInfo fixture was rejected");
  const auto sharedCarriedJson = json::parseFile(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "tests/fixtures/canonical_vf_info/v1_valid_loop_carried.json");
  if (!validateCanonicalVfInfo(
           decodeCanonicalVfInfoFixture(sharedCarriedJson)).ok())
    throw std::runtime_error("shared valid loop-carried fixture was rejected");

  using LoopPtr = std::variant_alternative_t<1, CanonicalNode::Payload>;
  static_assert(std::is_const_v<typename LoopPtr::element_type>,
                "canonical loop payload must be immutable when shared");

  CanonicalVfInfo invalidContract = canonicalContract;
  CanonicalMembar unsupportedMembar;
  unsupportedMembar.instructionId = "membar.invalid";
  unsupportedMembar.barrier = "ALL";
  invalidContract.context.push_back(
      CanonicalNode::makeMembar(std::move(unsupportedMembar)));
  const auto invalidResult = validateCanonicalVfInfo(invalidContract);
  if (invalidResult.ok() || invalidResult.diagnostics.back().code !=
                                "unsupported_membar_type")
    throw std::runtime_error("invalid native Membar was not diagnosed");

  const auto sharedInvalidJson = json::parseFile(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "tests/fixtures/canonical_vf_info/v1_invalid_loop_scope.json");
  const auto sharedInvalidResult = validateCanonicalVfInfo(
      decodeCanonicalVfInfoFixture(sharedInvalidJson));
  if (sharedInvalidResult.ok() || sharedInvalidResult.diagnostics.size() != 1 ||
      sharedInvalidResult.diagnostics.front().code !=
          "loop_back_edge_out_of_scope")
    throw std::runtime_error(
        "shared invalid fixture did not match Python diagnostic");

  CanonicalVfInfo nullLoopContract;
  nullLoopContract.context.push_back(
      CanonicalNode{std::shared_ptr<const CanonicalLoop>{}});
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

  CanonicalVfInfo nonFiniteContract;
  nonFiniteContract.uarch.emplace(
      "invalid", std::numeric_limits<double>::infinity());
  const auto nonFiniteResult = validateCanonicalVfInfo(nonFiniteContract);
  if (nonFiniteResult.ok() || nonFiniteResult.diagnostics.front().code !=
                                 "invalid_scalar_attribute")
    throw std::runtime_error("non-finite native scalar was not diagnosed");

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
