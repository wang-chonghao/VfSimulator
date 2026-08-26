// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER
// EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
// MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE in the root
// of the software repository for the full text of the License.

#include "native/IRPlanner.h"

#include "api/native/CanonicalVfInfo.h"
#include "api/native/InstructionCatalog.h"
#include "native/ParamDB.h"
#include "native/SimulatorRunner.h"

#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Diagnostics.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Value.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace vfsim {
namespace {

constexpr llvm::StringLiteral kLoopFusedAttr = "pto.vmi.loop_fused";
constexpr llvm::StringLiteral kLoopFusionIdAttr = "pto.vmi.loop_fusion.id";
constexpr llvm::StringLiteral kAabbccAttr = "pto.vfsim.aabbcc_unroll_factor";
constexpr llvm::StringLiteral kAbcabcAttr = "pto.vfsim.abcabc_unroll_factor";

enum class CandidateMode { Baseline, Abcabc, Aabbcc };

struct TypeInfo {
  CanonicalStorageKind storage = CanonicalStorageKind::Unknown;
  std::string dtype;
  std::vector<int64_t> shape;
  bool predicate = false;
  bool supported = false;
};

std::string printType(mlir::Type type) {
  std::string text;
  llvm::raw_string_ostream stream(text);
  type.print(stream);
  return text;
}

std::string normalizeDtype(llvm::StringRef dtype) {
  if (dtype == "f32")
    return "fp32";
  if (dtype == "f16")
    return "fp16";
  if (dtype == "bf16")
    return "bf16";
  if (dtype == "ui32" || dtype == "i32")
    return "b32";
  if (dtype == "ui16" || dtype == "i16")
    return "b16";
  if (dtype == "ui8" || dtype == "i8")
    return "b8";
  if (dtype == "si32")
    return "s32";
  if (dtype == "index")
    return "s64";
  return dtype.str();
}

std::optional<std::pair<int64_t, std::string>>
parseVectorPayload(llvm::StringRef payload) {
  const size_t separator = payload.find('x');
  if (separator == llvm::StringRef::npos)
    return std::nullopt;
  int64_t width = 0;
  if (payload.take_front(separator).getAsInteger(10, width) || width <= 0)
    return std::nullopt;
  return std::make_pair(width,
                        normalizeDtype(payload.drop_front(separator + 1)));
}

TypeInfo classifyType(mlir::Type type) {
  TypeInfo result;
  if (auto vector = mlir::dyn_cast<mlir::VectorType>(type)) {
    if (!vector.hasStaticShape() || vector.getRank() != 1)
      return result;
    result.storage = CanonicalStorageKind::Register;
    result.shape.assign(vector.getShape().begin(), vector.getShape().end());
    result.dtype = normalizeDtype(printType(vector.getElementType()));
    result.supported = true;
    return result;
  }
  if (auto floating = mlir::dyn_cast<mlir::FloatType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = normalizeDtype(printType(floating));
    result.supported = true;
    return result;
  }
  if (auto integer = mlir::dyn_cast<mlir::IntegerType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = integer.isSigned()
                       ? "s" + std::to_string(integer.getWidth())
                       : "b" + std::to_string(integer.getWidth());
    result.supported = true;
    return result;
  }
  if (mlir::isa<mlir::IndexType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = "s64";
    result.supported = true;
    return result;
  }

  const std::string typeText = printType(type);
  llvm::StringRef text(typeText);
  if (text.consume_front("!pto.vreg<") && text.consume_back(">")) {
    auto parsed = parseVectorPayload(text);
    if (!parsed)
      return result;
    result.storage = CanonicalStorageKind::Register;
    result.shape = {parsed->first};
    result.dtype = parsed->second;
    result.supported = true;
    return result;
  }

  text = llvm::StringRef(typeText);
  if (text.consume_front("!pto.ptr<") && text.consume_back(">")) {
    const size_t comma = text.find(',');
    if (comma == llvm::StringRef::npos ||
        text.drop_front(comma + 1).trim() != "ub")
      return result;
    result.storage = CanonicalStorageKind::UB;
    result.dtype = normalizeDtype(text.take_front(comma).trim());
    result.shape = {1};
    result.supported = true;
    return result;
  }

  text = llvm::StringRef(typeText);
  if (text.starts_with("!pto.mask<")) {
    result.predicate = true;
    result.supported = true;
  }
  return result;
}

std::optional<int64_t> constantInteger(mlir::Value value) {
  mlir::Operation *defining = value.getDefiningOp();
  if (defining == nullptr ||
      defining->getName().getStringRef() != "arith.constant")
    return std::nullopt;
  auto attribute = defining->getAttrOfType<mlir::IntegerAttr>("value");
  if (!attribute)
    return std::nullopt;
  return attribute.getInt();
}

std::optional<int64_t> staticTripCount(mlir::Operation *loop) {
  if (loop->getNumOperands() < 3 || loop->getNumRegions() != 1)
    return std::nullopt;
  const auto lower = constantInteger(loop->getOperand(0));
  const auto upper = constantInteger(loop->getOperand(1));
  const auto step = constantInteger(loop->getOperand(2));
  if (!lower || !upper || !step || *step <= 0 || *upper < *lower)
    return std::nullopt;
  const int64_t distance = *upper - *lower;
  if (distance % *step != 0)
    return std::nullopt;
  return distance / *step;
}

bool isVectorScope(mlir::Operation *operation) {
  const llvm::StringRef name = operation->getName().getStringRef();
  return name == "pto.vecscope" || name == "pto.vec_scope" ||
         name == "pto.strict_vecscope" || name == "pto.strict_vec_scope";
}

mlir::Operation *findVectorScope(mlir::Operation *operation) {
  for (mlir::Operation *parent = operation->getParentOp(); parent != nullptr;
       parent = parent->getParentOp())
    if (isVectorScope(parent))
      return parent;
  return nullptr;
}

CanonicalInstructionClass
instructionClass(CatalogInstructionClass catalogClass) {
  switch (catalogClass) {
  case CatalogInstructionClass::Load:
    return CanonicalInstructionClass::Load;
  case CatalogInstructionClass::Store:
    return CanonicalInstructionClass::Store;
  case CatalogInstructionClass::Compute:
    return CanonicalInstructionClass::Compute;
  case CatalogInstructionClass::Control:
    return CanonicalInstructionClass::Control;
  }
  return CanonicalInstructionClass::Unknown;
}

class VmiCanonicalAdapter {
public:
  VmiCanonicalAdapter(mlir::Operation *targetLoop, CandidateMode mode,
                      int64_t factor)
      : targetLoop_(targetLoop), mode_(mode), factor_(factor) {}

  std::optional<CanonicalVfInfo> build(mlir::Operation *vectorScope,
                                       std::string &reason) {
    if (vectorScope->getNumRegions() != 1 ||
        vectorScope->getRegion(0).empty()) {
      reason = "vector scope has no body region";
      return std::nullopt;
    }
    for (mlir::Operation &operation : vectorScope->getRegion(0).front()) {
      if (!convertOperation(&operation, info_.context, reason))
        return std::nullopt;
    }
    return std::move(info_);
  }

private:
  mlir::Operation *targetLoop_ = nullptr;
  CandidateMode mode_ = CandidateMode::Baseline;
  int64_t factor_ = 1;
  CanonicalVfInfo info_;
  llvm::DenseMap<mlir::Value, std::string> values_;
  int64_t nextValueId_ = 0;
  int64_t nextObjectId_ = 0;
  int64_t nextInstructionId_ = 0;
  int64_t nextLoopId_ = 0;

  std::string newValueId() { return "value." + std::to_string(nextValueId_++); }

  std::string getOrCreateValue(mlir::Value value, std::string &reason) {
    auto found = values_.find(value);
    if (found != values_.end())
      return found->second;
    const TypeInfo type = classifyType(value.getType());
    if (!type.supported || type.predicate) {
      reason = "unsupported value type " + printType(value.getType());
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue canonical;
    canonical.definitionId = id;
    canonical.logicalId = id;
    canonical.storage = type.storage;
    canonical.dtype = type.dtype;
    canonical.shape = type.shape;
    if (type.storage == CanonicalStorageKind::UB) {
      const std::string objectId = "ub." + std::to_string(nextObjectId_++);
      CanonicalStorageObject object;
      object.objectId = objectId;
      object.storage = CanonicalStorageKind::UB;
      object.shape = {1};
      info_.storageObjects.emplace(objectId, std::move(object));
      canonical.storageObjectId = objectId;
    }
    info_.values.emplace(id, std::move(canonical));
    values_[value] = id;
    return id;
  }

  std::string defineValue(mlir::Value value, const std::string &producer,
                          std::string &reason,
                          std::optional<std::string> logicalId = std::nullopt) {
    const TypeInfo type = classifyType(value.getType());
    if (!type.supported || type.predicate) {
      reason = "unsupported result type " + printType(value.getType());
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue canonical;
    canonical.definitionId = id;
    canonical.logicalId = logicalId.value_or(id);
    canonical.storage = type.storage;
    canonical.dtype = type.dtype;
    canonical.shape = type.shape;
    canonical.producerNodeId = producer;
    if (type.storage == CanonicalStorageKind::UB) {
      const std::string objectId = "ub." + std::to_string(nextObjectId_++);
      CanonicalStorageObject object;
      object.objectId = objectId;
      object.storage = CanonicalStorageKind::UB;
      object.shape = {1};
      info_.storageObjects.emplace(objectId, std::move(object));
      canonical.storageObjectId = objectId;
    }
    info_.values.emplace(id, std::move(canonical));
    values_[value] = id;
    return id;
  }

  std::string defineMemoryVersion(const std::string &baseId,
                                  const std::string &producer,
                                  std::string &reason) {
    auto base = info_.values.find(baseId);
    if (base == info_.values.end() ||
        base->second.storage != CanonicalStorageKind::UB ||
        !base->second.storageObjectId) {
      reason = "memory instruction has no stable UB base";
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue value = base->second;
    value.definitionId = id;
    value.producerNodeId = producer;
    info_.values.emplace(id, std::move(value));
    return id;
  }

  CanonicalOperand registerOperand(const std::string &id,
                                   CanonicalOperandRole role) const {
    CanonicalOperand operand;
    operand.valueId = id;
    operand.role = role;
    operand.dtype = info_.values.at(id).dtype;
    return operand;
  }

  CanonicalOperand memoryOperand(const std::string &id,
                                 CanonicalAccessKind access) const {
    CanonicalOperand operand;
    operand.valueId = id;
    operand.role = CanonicalOperandRole::Memory;
    operand.dtype = info_.values.at(id).dtype;
    CanonicalMemoryAccess memory;
    memory.baseObjectId = *info_.values.at(id).storageObjectId;
    memory.accessKind = access;
    memory.span = 1;
    operand.memoryAccess = std::move(memory);
    return operand;
  }

  bool aliasResult(mlir::Operation *operation, std::string &reason) {
    if (operation->getNumOperands() == 0 || operation->getNumResults() == 0) {
      reason = operation->getName().getStringRef().str() +
               " alias has no source/result";
      return false;
    }
    const std::string source =
        getOrCreateValue(operation->getOperand(0), reason);
    if (source.empty())
      return false;
    for (mlir::Value result : operation->getResults())
      values_[result] = source;
    return true;
  }

  bool convertPhysicalInstruction(mlir::Operation *operation,
                                  std::vector<CanonicalNode> &nodes,
                                  llvm::StringRef opcode, std::string form,
                                  std::string &reason) {
    const NativeInstructionSpec *spec =
        defaultInstructionCatalog().lookup(opcode.str());
    if (spec == nullptr) {
      reason = "VfSim catalog has no opcode " + opcode.str();
      return false;
    }
    const std::string specialized =
        defaultInstructionCatalog().specializeOpcode(opcode.str(), form);
    spec = defaultInstructionCatalog().lookup(specialized);
    if (spec == nullptr) {
      reason = "VfSim catalog has no specialized opcode " + specialized;
      return false;
    }

    CanonicalInstruction instruction;
    instruction.instructionId = "inst." + std::to_string(nextInstructionId_++);
    instruction.opcode = specialized;
    instruction.instructionClass = instructionClass(spec->instructionClass);
    instruction.form = std::move(form);

    const llvm::StringRef name = operation->getName().getStringRef();
    if (spec->instructionClass == CatalogInstructionClass::Load) {
      mlir::Value memoryValue;
      for (mlir::Value operand : operation->getOperands()) {
        if (classifyType(operand.getType()).storage ==
            CanonicalStorageKind::UB) {
          memoryValue = operand;
          break;
        }
      }
      if (!memoryValue || operation->getNumResults() == 0) {
        reason = name.str() + " requires UB input and register result";
        return false;
      }
      const std::string memoryId = getOrCreateValue(memoryValue, reason);
      if (memoryId.empty())
        return false;
      const std::string resultId = defineValue(
          operation->getResult(0), instruction.instructionId, reason);
      if (resultId.empty())
        return false;
      instruction.inputs.push_back(
          memoryOperand(memoryId, CanonicalAccessKind::Read));
      instruction.outputs.push_back(
          registerOperand(resultId, CanonicalOperandRole::Destination));
    } else if (spec->instructionClass == CatalogInstructionClass::Store) {
      mlir::Value sourceValue;
      mlir::Value memoryValue;
      for (mlir::Value operand : operation->getOperands()) {
        const TypeInfo type = classifyType(operand.getType());
        if (!sourceValue && type.storage == CanonicalStorageKind::Register)
          sourceValue = operand;
        if (!memoryValue && type.storage == CanonicalStorageKind::UB)
          memoryValue = operand;
      }
      if (!sourceValue || !memoryValue) {
        reason = name.str() + " requires register source and UB output";
        return false;
      }
      const std::string sourceId = getOrCreateValue(sourceValue, reason);
      const std::string memoryId = getOrCreateValue(memoryValue, reason);
      if (sourceId.empty() || memoryId.empty())
        return false;
      const std::string outputId =
          defineMemoryVersion(memoryId, instruction.instructionId, reason);
      if (outputId.empty())
        return false;
      instruction.inputs.push_back(
          registerOperand(sourceId, CanonicalOperandRole::Source));
      instruction.outputs.push_back(
          memoryOperand(outputId, CanonicalAccessKind::Write));
      for (mlir::Value result : operation->getResults())
        values_[result] = outputId;
    } else {
      llvm::SmallVector<mlir::Value> modeledOperands;
      for (mlir::Value operand : operation->getOperands()) {
        const TypeInfo type = classifyType(operand.getType());
        if (!type.supported || type.predicate)
          continue;
        if (type.storage != CanonicalStorageKind::Register &&
            type.storage != CanonicalStorageKind::Scalar)
          continue;
        modeledOperands.push_back(operand);
      }
      llvm::SmallVector<const NativeOperandSpec *> expectedInputs;
      llvm::SmallVector<const NativeOperandSpec *> expectedOutputs;
      for (const NativeOperandSpec &operand : spec->operands) {
        if (operand.direction == CatalogOperandDirection::Input)
          expectedInputs.push_back(&operand);
        else if (operand.direction == CatalogOperandDirection::Output)
          expectedOutputs.push_back(&operand);
      }
      if (modeledOperands.size() != expectedInputs.size()) {
        reason = name.str() + " operand count does not match VfSim Catalog";
        return false;
      }
      for (auto [operand, expected] :
           llvm::zip_equal(modeledOperands, expectedInputs)) {
        const std::string id = getOrCreateValue(operand, reason);
        if (id.empty())
          return false;
        instruction.inputs.push_back(registerOperand(
            id, expected->role == "scalar" ? CanonicalOperandRole::Scalar
                                           : CanonicalOperandRole::Source));
      }
      size_t outputIndex = 0;
      for (mlir::Value result : operation->getResults()) {
        const TypeInfo type = classifyType(result.getType());
        if (type.storage != CanonicalStorageKind::Register)
          continue;
        const std::string id =
            defineValue(result, instruction.instructionId, reason);
        if (id.empty())
          return false;
        instruction.outputs.push_back(
            registerOperand(id, CanonicalOperandRole::Destination));
        ++outputIndex;
      }
      if (outputIndex != expectedOutputs.size()) {
        reason = name.str() + " result count does not match VfSim Catalog";
        return false;
      }
    }

    nodes.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
    return true;
  }

  bool convertLoop(mlir::Operation *operation,
                   std::vector<CanonicalNode> &nodes, std::string &reason) {
    const auto tripCount = staticTripCount(operation);
    if (!tripCount || *tripCount < 0 || operation->getNumRegions() != 1 ||
        operation->getRegion(0).empty()) {
      reason = "scf.for requires a static positive-step trip count";
      return false;
    }
    mlir::Block &body = operation->getRegion(0).front();
    const size_t carriedCount = operation->getNumOperands() - 3;
    if (body.getNumArguments() != carriedCount + 1 ||
        operation->getNumResults() != carriedCount) {
      reason = "scf.for operand, block argument, and result counts disagree";
      return false;
    }

    const bool isTarget = operation == targetLoop_;
    const int64_t repeats =
        isTarget && mode_ == CandidateMode::Abcabc ? factor_ : 1;
    CanonicalLoop loop;
    loop.loopId = "loop." + std::to_string(nextLoopId_++);
    loop.induction.variableId = loop.loopId + ".iv";
    loop.induction.start =
        constantInteger(operation->getOperand(0)).value_or(0);
    loop.induction.step = constantInteger(operation->getOperand(2)).value_or(1);
    loop.count = isTarget && mode_ == CandidateMode::Abcabc
                     ? *tripCount / factor_
                     : *tripCount;
    loop.unroll = isTarget && mode_ == CandidateMode::Aabbcc ? factor_ : 1;

    std::vector<std::string> entries;
    std::vector<std::string> current;
    entries.reserve(carriedCount);
    current.reserve(carriedCount);
    for (size_t index = 0; index < carriedCount; ++index) {
      const std::string id =
          getOrCreateValue(operation->getOperand(index + 3), reason);
      if (id.empty())
        return false;
      entries.push_back(id);
      current.push_back(id);
    }

    for (int64_t lane = 0; lane < repeats; ++lane) {
      for (size_t index = 0; index < carriedCount; ++index)
        values_[body.getArgument(index + 1)] = current[index];
      for (mlir::Operation &bodyOperation : body.without_terminator()) {
        if (!convertOperation(&bodyOperation, loop.body, reason))
          return false;
      }
      mlir::Operation *terminator = body.getTerminator();
      if (terminator->getName().getStringRef() != "scf.yield" ||
          terminator->getNumOperands() != carriedCount) {
        reason = "scf.for terminator must be scf.yield with all carried values";
        return false;
      }
      for (size_t index = 0; index < carriedCount; ++index) {
        current[index] =
            getOrCreateValue(terminator->getOperand(index), reason);
        if (current[index].empty())
          return false;
      }
    }

    for (size_t index = 0; index < carriedCount; ++index) {
      const CanonicalValue &entry = info_.values.at(entries[index]);
      const std::string exitId = defineValue(
          operation->getResult(index), loop.loopId, reason, entry.logicalId);
      if (exitId.empty())
        return false;
      CanonicalValue &exit = info_.values.at(exitId);
      exit.storageObjectId = entry.storageObjectId;
      CanonicalLoopCarriedValue carried;
      carried.logicalId = entry.logicalId;
      carried.entryValueId = entries[index];
      carried.backEdgeValueId = current[index];
      carried.exitValueId = exitId;
      loop.carriedValues.push_back(std::move(carried));
    }

    nodes.push_back(CanonicalNode::makeLoop(std::move(loop)));
    return true;
  }

  bool convertOperation(mlir::Operation *operation,
                        std::vector<CanonicalNode> &nodes,
                        std::string &reason) {
    const llvm::StringRef name = operation->getName().getStringRef();
    if (name == "scf.for")
      return convertLoop(operation, nodes, reason);
    if (name == "scf.yield" || name == "pto.yield" ||
        name == "pto.vecscope_yield")
      return true;
    if (name == "pto.vbitcast")
      return aliasResult(operation, reason);
    if (name == "pto.addptr" || name == "pto.castptr")
      return aliasResult(operation, reason);
    if (name.starts_with("arith.") || name == "pto.pset_b16" ||
        name == "pto.pset_b32" || name == "pto.pge_b16" ||
        name == "pto.pge_b32")
      return true;

    llvm::StringRef opcode;
    if (name == "pto.vlds")
      opcode = "VLDS";
    else if (name == "pto.vsts")
      opcode = "VSTS";
    else if (name == "pto.vsstb")
      opcode = "VSSTB";
    else if (name == "pto.vadd")
      opcode = "VADD";
    else if (name == "pto.vsub")
      opcode = "VSUB";
    else if (name == "pto.vadds")
      opcode = "VADDS";
    else if (name == "pto.vmuls")
      opcode = "VMULS";
    else if (name == "pto.vmaxs")
      opcode = "VMAXS";
    else if (name == "pto.vmins")
      opcode = "VMINS";
    else if (name == "pto.vmul")
      opcode = "VMUL";
    else if (name == "pto.vmax")
      opcode = "VMAX";
    else if (name == "pto.vmin")
      opcode = "VMIN";
    else if (name == "pto.vdiv")
      opcode = "VDIV";
    else if (name == "pto.vand")
      opcode = "VAND";
    else if (name == "pto.vexp")
      opcode = "VEXP";
    else if (name == "pto.vabs")
      opcode = "VABS";
    else if (name == "pto.vdup")
      opcode = "VDUP";
    else if (name == "pto.vcmax")
      opcode = "VCMAX";
    else if (name == "pto.vcmin")
      opcode = "VCMIN";
    else if (name == "pto.vcadd")
      opcode = "VCADD";
    else if (name == "pto.vcgmax")
      opcode = "VCGMAX";
    else if (name == "pto.vsel")
      opcode = "VSEL";
    else if (name == "pto.vshls")
      opcode = "VSHLS";
    else if (name == "pto.vshrs")
      opcode = "VSHRS";
    else if (name == "pto.vbr")
      opcode = "VBR";
    else if (name == "pto.vexpdif")
      opcode = "VEXPDIF";
    else if (name == "pto.vcvt")
      opcode = "VCVT";
    else if (name == "pto.vpack")
      opcode = "VPACK";
    else {
      reason = "unsupported operation " + name.str();
      return false;
    }

    std::string form;
    if (opcode == "VSSTB") {
      form = "b16";
    } else if (opcode == "VPACK") {
      form = "b32";
    } else if (opcode == "VCVT") {
      if (operation->getNumOperands() == 0 || operation->getNumResults() == 0) {
        reason = "pto.vcvt requires source and result";
        return false;
      }
      const TypeInfo source = classifyType(operation->getOperand(0).getType());
      const TypeInfo result = classifyType(operation->getResult(0).getType());
      form = source.dtype + "_to_" + result.dtype;
      if (form == "fp32_to_bf16")
        form = "f32_to_bf16";
      else if (form == "fp32_to_fp16")
        form = "f32_to_f16";
      else if (form == "fp16_to_fp32")
        form = "f16_to_f32";
      else if (form == "fp32_to_s32")
        form = "f32_to_s32";
      else if (form == "s32_to_fp32")
        form = "s32_to_f32";
    } else {
      mlir::Type formType;
      if (opcode == "VLDS" && operation->getNumResults() != 0)
        formType = operation->getResult(0).getType();
      else if (opcode == "VSTS" && operation->getNumOperands() != 0)
        formType = operation->getOperand(0).getType();
      else if (operation->getNumResults() != 0)
        formType = operation->getResult(0).getType();
      if (!formType) {
        reason = name.str() + " has no form-bearing type";
        return false;
      }
      form = classifyType(formType).dtype;
    }
    return convertPhysicalInstruction(operation, nodes, opcode, std::move(form),
                                      reason);
  }
};

struct CandidateResult {
  CandidateMode mode = CandidateMode::Baseline;
  int64_t factor = 1;
  int64_t cycles = 0;
};

llvm::StringRef modeName(CandidateMode mode) {
  switch (mode) {
  case CandidateMode::Baseline:
    return "NO_UNROLL";
  case CandidateMode::Abcabc:
    return "ABCABC";
  case CandidateMode::Aabbcc:
    return "AABBCC";
  }
  return "UNKNOWN";
}

std::optional<CandidateResult>
evaluateCandidate(mlir::Operation *vectorScope, mlir::Operation *targetLoop,
                  CandidateMode mode, int64_t factor, const ParamDB &db,
                  std::string &reason) {
  VmiCanonicalAdapter adapter(targetLoop, mode, factor);
  auto vfInfo = adapter.build(vectorScope, reason);
  if (!vfInfo)
    return std::nullopt;
  try {
    const SimulationResult result = runCanonicalVfInfo(*vfInfo, db);
    return CandidateResult{mode, factor,
                           std::max(result.vfEndCycle, result.cyclesExecuted)};
  } catch (const std::exception &error) {
    reason = error.what();
    return std::nullopt;
  }
}

void writePlan(mlir::Operation *loop, CandidateMode mode, int64_t factor) {
  mlir::Builder builder(loop->getContext());
  int64_t abcabc = 1;
  int64_t aabbcc = 1;
  if (mode == CandidateMode::Abcabc)
    abcabc = factor;
  else if (mode == CandidateMode::Aabbcc)
    aabbcc = factor;
  loop->setAttr(kAbcabcAttr, builder.getI64IntegerAttr(abcabc));
  loop->setAttr(kAabbccAttr, builder.getI64IntegerAttr(aabbcc));
}

} // namespace

mlir::LogicalResult planTileFusionIR(mlir::Operation *candidateIR,
                                     const PlannerOptions &options) {
  (void)options;
  if (candidateIR == nullptr)
    return mlir::failure();

  // First source-level integration step: establish the IR protocol entry point.
  // The planner implementation will later read candidate tileop IR and write
  // fusion attrs back to the same IR.
  return mlir::success();
}

mlir::LogicalResult planVmiUnrollIR(mlir::Operation *scope,
                                    const PlannerOptions &options) {
  if (scope == nullptr)
    return mlir::failure();

  std::unique_ptr<ParamDB> db;
  try {
    db = std::make_unique<ParamDB>(std::filesystem::path(VFSIM_SOURCE_ROOT));
  } catch (const std::exception &error) {
    scope->emitError() << "VfSim failed to load its parameter database: "
                       << error.what();
    return mlir::failure();
  }
  llvm::SmallVector<mlir::Operation *> candidateLoops;
  scope->walk([&](mlir::Operation *operation) {
    if (operation->getName().getStringRef() == "scf.for" &&
        operation->hasAttr(kLoopFusedAttr) &&
        operation->hasAttr(kLoopFusionIdAttr))
      candidateLoops.push_back(operation);
  });

  for (mlir::Operation *loop : candidateLoops) {
    const auto tripCount = staticTripCount(loop);
    if (!tripCount || *tripCount <= 1) {
      loop->emitWarning()
          << "VfSim skipped VMI fused loop: trip count is not a static "
             "integer greater than one";
      continue;
    }
    mlir::Operation *vectorScope = findVectorScope(loop);
    if (vectorScope == nullptr) {
      loop->emitWarning()
          << "VfSim skipped VMI fused loop: no enclosing vector scope";
      continue;
    }

    std::vector<int64_t> factors;
    const int64_t upper =
        std::min<int64_t>(options.maxUnrollFactor, *tripCount);
    for (int64_t factor = 2; factor <= upper; ++factor)
      if (*tripCount % factor == 0)
        factors.push_back(factor);
    if (factors.empty()) {
      if (options.dumpCandidates)
        llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                     << " skipped: no divisible factor in [2, "
                     << options.maxUnrollFactor << "]\n";
      continue;
    }

    std::string reason;
    auto baseline = evaluateCandidate(vectorScope, loop,
                                      CandidateMode::Baseline, 1, *db, reason);
    if (!baseline) {
      loop->emitWarning() << "VfSim skipped VMI fused loop: " << reason;
      continue;
    }
    CandidateResult best = *baseline;
    if (options.dumpCandidates)
      llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                   << " candidate mode=NO_UNROLL factor=1 cycles="
                   << baseline->cycles << "\n";

    bool allCandidatesValid = true;
    for (int64_t factor : factors) {
      for (CandidateMode mode :
           {CandidateMode::Abcabc, CandidateMode::Aabbcc}) {
        if (mode == CandidateMode::Aabbcc && factor == *tripCount)
          continue;
        reason.clear();
        auto candidate =
            evaluateCandidate(vectorScope, loop, mode, factor, *db, reason);
        if (!candidate) {
          loop->emitWarning()
              << "VfSim skipped VMI fused loop candidate " << modeName(mode)
              << " factor=" << factor << ": " << reason;
          allCandidatesValid = false;
          break;
        }
        if (options.dumpCandidates)
          llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                       << " candidate mode=" << modeName(mode)
                       << " factor=" << factor
                       << " cycles=" << candidate->cycles << "\n";
        if (candidate->cycles < best.cycles)
          best = *candidate;
      }
      if (!allCandidatesValid)
        break;
    }
    if (!allCandidatesValid)
      continue;

    writePlan(loop, best.mode, best.factor);
    if (options.dumpCandidates)
      llvm::errs() << "[VfSim] selected mode=" << modeName(best.mode)
                   << " factor=" << best.factor << " cycles=" << best.cycles
                   << "\n";
  }
  return mlir::success();
}

} // namespace vfsim
