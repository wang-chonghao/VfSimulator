// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/CanonicalVfInfo.h"
#include "api/native/InstructionCatalog.h"
#include "api/native/VfInfo.h"
#include "native/ParamDB.h"
#include "native/Json.h"
#include "native/CanonicalVfInfoFixtureDecoder.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
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

bool hasDiagnostic(const CanonicalValidationResult &result,
                   const std::string &code) {
  for (const auto &diagnostic : result.diagnostics)
    if (diagnostic.code == code)
      return true;
  return false;
}

std::string readText(const std::filesystem::path &path) {
  std::ifstream stream(path);
  std::ostringstream text;
  text << stream.rdbuf();
  return text.str();
}

} // namespace

int main() {
  const auto &instructionCatalog = defaultInstructionCatalog();
  if (instructionCatalog.canonicalOpcode("vld") != "VLDS" ||
      instructionCatalog.canonicalOpcode("vexpdif") != "VEXPDIF" ||
      instructionCatalog.specializeOpcode("vcvt", "f32_to_bf16") !=
          "VCVT_F32_TO_BF16" ||
      instructionCatalog.lookup("VSSTB") == nullptr ||
      instructionCatalog.lookup("VSSTB")->instructionClass !=
          CatalogInstructionClass::Store ||
      !instructionCatalog.lookup("VADD")->forms.count("b16") ||
      instructionCatalog.lookup("VADD")->signature != "binary" ||
      instructionCatalog.lookup("VADD")->operands.size() != 4 ||
      instructionCatalog.lookup("VADD")->operands[2].kind !=
          CatalogArgumentKind::Register ||
      instructionCatalog.lookup("VLDS")->operands[3].allowedValues.count(
          "NORM") != 1 ||
      instructionCatalog.lookup("VLDS")->callVariants.size() != 2 ||
      !instructionCatalog.lookup("VLDS")->operands[2].allowIntegerExpression ||
      instructionCatalog.lookup("VLDS")->operands[3].allowIntegerExpression ||
      instructionCatalog.lookup("VDUP")->callVariants[1].argumentValues.at(3).count(
          "POS_LOWEST") != 1)
    throw std::runtime_error("native generated instruction catalog mismatch");

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
  CanonicalVfInfo canonicalCarriedContract =
      decodeCanonicalVfInfoFixture(sharedCarriedJson);
  if (!validateCanonicalVfInfo(canonicalCarriedContract).ok())
    throw std::runtime_error("shared valid loop-carried fixture was rejected");
  CanonicalVfInfo aliasBackEdgeContract = canonicalCarriedContract;
  const auto aliasLoop = std::get<std::shared_ptr<const CanonicalLoop>>(
      aliasBackEdgeContract.context.front().payload);
  const std::string aliasBackEdgeId =
      aliasLoop->carriedValues.front().backEdgeValueId;
  aliasBackEdgeContract.values.at(aliasBackEdgeId).logicalId = "alias.source";
  if (!validateCanonicalVfInfo(aliasBackEdgeContract).ok())
    throw std::runtime_error(
        "native validator rejected a type-compatible alias back-edge");
  aliasBackEdgeContract.values.at(aliasBackEdgeId).dtype = "fp16";
  if (!hasDiagnostic(validateCanonicalVfInfo(aliasBackEdgeContract),
                     "loop_carried_type_mismatch"))
    throw std::runtime_error(
        "native validator accepted an alias back-edge type mismatch");
  CanonicalVfInfo invariantBackEdgeContract = canonicalCarriedContract;
  CanonicalLoop invariantLoop =
      *std::get<std::shared_ptr<const CanonicalLoop>>(
          invariantBackEdgeContract.context.front().payload);
  invariantLoop.carriedValues.front().backEdgeValueId = "rhs.entry";
  invariantBackEdgeContract.context.front() =
      CanonicalNode::makeLoop(std::move(invariantLoop));
  if (!validateCanonicalVfInfo(invariantBackEdgeContract).ok())
    throw std::runtime_error(
        "native validator rejected an invariant alias back-edge");

  const ParamDB db(std::filesystem::path(VFSIM_SOURCE_ROOT));
  const auto canonicalOutDir =
      std::filesystem::temp_directory_path() / "vfsim_native_canonical_api";
  const SimulationResult canonicalResult =
      runCanonicalVfInfo(canonicalContract, db, canonicalOutDir.string());
  if (canonicalResult.cyclesExecuted != 35 || canonicalResult.vfEndCycle != 47)
    throw std::runtime_error(
        "native canonical loop result differs from Python: cycles=" +
        std::to_string(canonicalResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalResult.vfEndCycle));
  const std::string canonicalHistory =
      readText(canonicalOutDir / "sim_history.json");
  if (canonicalHistory.find("\"static_instruction_id\":\"inst.load\"") ==
          std::string::npos ||
      canonicalHistory.find(
          "\"iteration_path\":[{\"loop_id\":\"loop.row\",\"iteration\":0}]") ==
          std::string::npos ||
      canonicalHistory.find("\"stream_seq\":0") == std::string::npos)
    throw std::runtime_error(
        "native canonical dynamic identity was not preserved in history");
  const SimulationResult canonicalCarriedResult =
      runCanonicalVfInfo(canonicalCarriedContract, db);
  if (canonicalCarriedResult.cyclesExecuted != 40 ||
      canonicalCarriedResult.vfEndCycle != 52)
    throw std::runtime_error(
        "native canonical loop-carried result differs from Python: cycles=" +
        std::to_string(canonicalCarriedResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalCarriedResult.vfEndCycle));
  CanonicalVfInfo canonicalUnrolledContract = canonicalCarriedContract;
  CanonicalLoop unrolledLoop = *std::get<std::shared_ptr<const CanonicalLoop>>(
      canonicalUnrolledContract.context.front().payload);
  unrolledLoop.unroll = int64_t{2};
  canonicalUnrolledContract.context.front() =
      CanonicalNode::makeLoop(std::move(unrolledLoop));
  const SimulationResult canonicalUnrolledResult =
      runCanonicalVfInfo(canonicalUnrolledContract, db);
  if (canonicalUnrolledResult.cyclesExecuted != 40 ||
      canonicalUnrolledResult.vfEndCycle != 52)
    throw std::runtime_error(
        "native canonical structured unroll differs from Python: cycles=" +
        std::to_string(canonicalUnrolledResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalUnrolledResult.vfEndCycle));
  CanonicalVfInfo canonicalMembarUnrollContract = canonicalCarriedContract;
  CanonicalLoop membarUnrolledLoop =
      *std::get<std::shared_ptr<const CanonicalLoop>>(
          canonicalMembarUnrollContract.context.front().payload);
  membarUnrolledLoop.unroll = int64_t{2};
  CanonicalMembar loopMembar;
  loopMembar.instructionId = "membar.loop";
  loopMembar.barrier = "VST_VLD";
  membarUnrolledLoop.body.push_back(
      CanonicalNode::makeMembar(std::move(loopMembar)));
  canonicalMembarUnrollContract.context.front() =
      CanonicalNode::makeLoop(std::move(membarUnrolledLoop));
  const ParamDB warningDb(std::filesystem::path(VFSIM_SOURCE_ROOT));
  (void)runCanonicalVfInfo(canonicalMembarUnrollContract, warningDb);
  bool sawMembarUnrollWarning = false;
  for (const auto &warning : warningDb.warnings())
    sawMembarUnrollWarning =
        sawMembarUnrollWarning || warning.kind == "membar_unroll_disabled";
  if (!sawMembarUnrollWarning)
    throw std::runtime_error(
        "native canonical membar unroll fallback did not record warning");
  CanonicalVfInfo canonicalUarchContract = canonicalContract;
  canonicalUarchContract.uarch["vreg_num"] = int64_t{1};
  const SimulationResult canonicalUarchResult =
      runCanonicalVfInfo(canonicalUarchContract, db);
  if (canonicalUarchResult.cyclesExecuted != 68 ||
      canonicalUarchResult.vfEndCycle != 80)
    throw std::runtime_error(
        "native canonical uarch override differs from Python: cycles=" +
        std::to_string(canonicalUarchResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalUarchResult.vfEndCycle));
  CanonicalVfInfo deprecatedUarchContract = canonicalContract;
  deprecatedUarchContract.uarch["load_done_latency"] = int64_t{99};
  if (!hasDiagnostic(validateCanonicalVfInfo(deprecatedUarchContract),
                     "deprecated_uarch_field"))
    throw std::runtime_error(
        "native validator accepted deprecated load_done_latency override");
  for (const auto &[name, value] :
       std::vector<std::pair<std::string, CanonicalScalar>>{
           {"issue_ports", std::string("2")},
           {"issue_ports", true},
           {"three_ports_mode", int64_t{1}},
           {"three_ports_mode", std::string("true")},
           {"shq_exq_dispatch_policy", int64_t{1}},
           {"shq_exq_dispatch_policy", false},
       }) {
    CanonicalVfInfo invalidUarchTypeContract = canonicalContract;
    invalidUarchTypeContract.uarch[name] = value;
    if (!hasDiagnostic(validateCanonicalVfInfo(invalidUarchTypeContract),
                       "uarch_field_type_mismatch"))
      throw std::runtime_error(
          "native validator accepted wrong uarch field type: " + name);
  }

  CanonicalVfInfo nonInnermostUnrollContract;
  CanonicalLoop outerLoop;
  outerLoop.loopId = "loop.outer";
  outerLoop.induction.variableId = "i";
  outerLoop.count = int64_t{2};
  outerLoop.unroll = int64_t{2};
  CanonicalLoop innerLoop;
  innerLoop.loopId = "loop.inner";
  innerLoop.induction.variableId = "j";
  innerLoop.count = int64_t{2};
  outerLoop.body.push_back(CanonicalNode::makeLoop(std::move(innerLoop)));
  nonInnermostUnrollContract.context.push_back(
      CanonicalNode::makeLoop(std::move(outerLoop)));
  bool rejectedNonInnermostUnroll = false;
  try {
    (void)runCanonicalVfInfo(nonInnermostUnrollContract, db);
  } catch (const std::runtime_error &error) {
    rejectedNonInnermostUnroll =
        std::string(error.what()).find("non-innermost") != std::string::npos;
  }
  if (!rejectedNonInnermostUnroll)
    throw std::runtime_error(
        "native canonical non-innermost unroll was not rejected");

  CanonicalVfInfo ghostContract = canonicalContract;
  CanonicalValue ghostValue;
  ghostValue.definitionId = "ghost.0";
  ghostValue.logicalId = "ghost";
  ghostValue.storage = CanonicalStorageKind::Register;
  ghostValue.dtype = "fp32";
  ghostValue.producerNodeId = "inst.load";
  ghostContract.values.emplace(ghostValue.definitionId, std::move(ghostValue));
  if (!hasDiagnostic(validateCanonicalVfInfo(ghostContract),
                     "producer_definition_not_emitted"))
    throw std::runtime_error("native ghost definition was not diagnosed");

  CanonicalVfInfo classContract = canonicalContract;
  const auto originalLoop =
      std::get<std::shared_ptr<const CanonicalLoop>>(
          classContract.context.front().payload);
  CanonicalLoop computeLoop = *originalLoop;
  CanonicalInstruction computeWithMemory =
      std::get<CanonicalInstruction>(computeLoop.body.front().payload);
  computeWithMemory.instructionClass = CanonicalInstructionClass::Compute;
  computeLoop.body.front() =
      CanonicalNode::makeInstruction(std::move(computeWithMemory));
  classContract.context.front() = CanonicalNode::makeLoop(std::move(computeLoop));
  if (!hasDiagnostic(validateCanonicalVfInfo(classContract),
                     "instruction_class_memory_access_mismatch"))
    throw std::runtime_error("native class/access mismatch was not diagnosed");

  CanonicalVfInfo catalogContract = canonicalContract;
  CanonicalLoop catalogLoop = *originalLoop;
  CanonicalInstruction catalogMismatch =
      std::get<CanonicalInstruction>(catalogLoop.body.front().payload);
  catalogMismatch.opcode = "VEXPDIF";
  catalogMismatch.form = "fp16";
  catalogLoop.body.front() =
      CanonicalNode::makeInstruction(std::move(catalogMismatch));
  catalogContract.context.front() =
      CanonicalNode::makeLoop(std::move(catalogLoop));
  const auto catalogResult = validateCanonicalVfInfo(catalogContract);
  if (!hasDiagnostic(catalogResult, "catalog_instruction_class_mismatch") ||
      !hasDiagnostic(catalogResult, "catalog_instruction_form_mismatch"))
    throw std::runtime_error("native Catalog semantics mismatch was not diagnosed");

  if (CanonicalInstruction{}.instructionClass !=
          CanonicalInstructionClass::Unknown ||
      CanonicalOperand{}.role != CanonicalOperandRole::Unknown ||
      CanonicalMemoryAccess{}.accessKind != CanonicalAccessKind::Unknown ||
      CanonicalDependencyRef{}.kind != CanonicalDependencyKind::Unknown ||
      CanonicalStorageObject{}.storage != CanonicalStorageKind::Unknown ||
      CanonicalValue{}.storage != CanonicalStorageKind::Unknown)
    throw std::runtime_error("native required enum defaults must be Unknown");
  CanonicalVfInfo missingClassContract;
  CanonicalInstruction missingClass;
  missingClass.instructionId = "inst.missing_class";
  missingClass.opcode = "VUNKNOWN";
  missingClass.form = "fp32";
  missingClassContract.context.push_back(
      CanonicalNode::makeInstruction(std::move(missingClass)));
  if (!hasDiagnostic(validateCanonicalVfInfo(missingClassContract),
                     "missing_instruction_class"))
    throw std::runtime_error("native missing instruction class was not diagnosed");

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
