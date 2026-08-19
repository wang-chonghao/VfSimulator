// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/LegacyVfInfoAdapter.h"

#include "api/native/InstructionCatalog.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <unordered_set>

namespace vfsim {
namespace {

using Environment = std::unordered_map<std::string, std::string>;

std::string safeId(const std::string &value) {
  std::string out;
  out.reserve(value.size());
  for (unsigned char c : value)
    out.push_back(std::isalnum(c) || c == '_' || c == '.' || c == '-'
                      ? static_cast<char>(c)
                      : '_');
  return out.empty() ? "value" : out;
}

CanonicalStorageKind canonicalStorage(ValueStorageKind storage) {
  switch (storage) {
  case ValueStorageKind::Register:
    return CanonicalStorageKind::Register;
  case ValueStorageKind::UB:
    return CanonicalStorageKind::UB;
  case ValueStorageKind::Scalar:
    return CanonicalStorageKind::Scalar;
  }
  return CanonicalStorageKind::Unknown;
}

CanonicalInstructionClass canonicalClass(CatalogInstructionClass value) {
  switch (value) {
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

CanonicalOperandRole canonicalRole(const std::string &value) {
  if (value == "source")
    return CanonicalOperandRole::Source;
  if (value == "destination")
    return CanonicalOperandRole::Destination;
  if (value == "memory")
    return CanonicalOperandRole::Memory;
  if (value == "scalar")
    return CanonicalOperandRole::Scalar;
  if (value == "predicate")
    return CanonicalOperandRole::Predicate;
  if (value == "config")
    return CanonicalOperandRole::Config;
  return CanonicalOperandRole::Unknown;
}

class Adapter {
public:
  explicit Adapter(VfInfo input) : input_(std::move(input)) {
    output_.params = input_.params;
    output_.source.emplace("adapter", std::string("legacy_vf_info"));
  }

  CanonicalVfInfo run() {
    Environment environment;
    output_.context = versionNodes(input_.body, environment);
    const auto validation = validateCanonicalVfInfo(output_);
    if (!validation.ok()) {
      std::string message = "Legacy VfInfo could not be adapted to CanonicalVfInfo";
      for (const auto &diagnostic : validation.diagnostics)
        message += "; " + diagnostic.code + " at " + diagnostic.path;
      throw std::runtime_error(message);
    }
    return output_;
  }

private:
  VfInfo input_;
  CanonicalVfInfo output_;
  int64_t instructionCounter_ = 0;
  int64_t loopCounter_ = 0;
  int64_t membarCounter_ = 0;
  int64_t definitionCounter_ = 0;
  int64_t omittedScalarCounter_ = 0;
  std::unordered_set<std::string> nodeIds_;

  std::string nodeId(const std::string &kind, const std::string &preferred = "") {
    if (!preferred.empty()) {
      if (!nodeIds_.insert(preferred).second)
        throw std::runtime_error("duplicate legacy node ID: " + preferred);
      return preferred;
    }
    int64_t *counter = kind == "instruction" ? &instructionCounter_
                       : kind == "loop"       ? &loopCounter_
                                               : &membarCounter_;
    while (true) {
      const std::string candidate = kind + "." + std::to_string((*counter)++);
      if (nodeIds_.insert(candidate).second)
        return candidate;
    }
  }

  const ValueInfo &logicalValue(const std::string &logicalId) const {
    const auto found = input_.values.find(logicalId);
    if (found == input_.values.end())
      throw std::runtime_error("legacy operand has no value metadata: " + logicalId);
    return found->second;
  }

  std::string newDefinition(const std::string &logicalId,
                            const std::optional<std::string> &producer) {
    const ValueInfo &logical = logicalValue(logicalId);
    const std::string definitionId =
        safeId(logicalId) + ".def" + std::to_string(definitionCounter_++);
    CanonicalValue value;
    value.definitionId = definitionId;
    value.logicalId = logicalId;
    value.storage = canonicalStorage(logical.storage);
    value.dtype = logical.dtype.empty() ? input_.defaultDtype : logical.dtype;
    value.shape = logical.shape;
    value.producerNodeId = producer;
    if (logical.storage == ValueStorageKind::UB) {
      const std::string objectId = "ub." + safeId(logicalId);
      value.storageObjectId = objectId;
      output_.storageObjects.try_emplace(
          objectId,
          CanonicalStorageObject{objectId, CanonicalStorageKind::UB,
                                 logical.shape, std::nullopt});
    }
    output_.values.emplace(definitionId, std::move(value));
    return definitionId;
  }

  std::string ensureEntry(const std::string &logicalId, Environment &environment) {
    auto found = environment.find(logicalId);
    if (found != environment.end())
      return found->second;
    const std::string definitionId = newDefinition(logicalId, std::nullopt);
    environment.emplace(logicalId, definitionId);
    return definitionId;
  }

  CanonicalOperand operand(const std::string &definitionId,
                           const std::string &logicalId,
                           CanonicalOperandRole role, bool output) const {
    const ValueInfo &logical = logicalValue(logicalId);
    CanonicalOperand result;
    result.valueId = definitionId;
    result.role = role;
    result.dtype = logical.dtype.empty() ? input_.defaultDtype : logical.dtype;
    if (logical.storage == ValueStorageKind::UB) {
      CanonicalMemoryAccess memory;
      memory.baseObjectId = "ub." + safeId(logicalId);
      memory.accessKind = output ? CanonicalAccessKind::Write
                                 : CanonicalAccessKind::Read;
      result.memoryAccess = std::move(memory);
    }
    return result;
  }

  std::vector<const NativeOperandSpec *>
  trackedOperands(const NativeInstructionSpec *spec,
                  CatalogOperandDirection direction) const {
    std::vector<const NativeOperandSpec *> result;
    if (spec == nullptr)
      return result;
    for (const auto &item : spec->operands)
      if (item.direction == direction)
        result.push_back(&item);
    std::sort(result.begin(), result.end(), [](const auto *lhs, const auto *rhs) {
      return lhs->argumentIndex < rhs->argumentIndex;
    });
    return result;
  }

  CanonicalInstruction versionInstruction(const ProgramInstNode &inst,
                                          Environment &environment) {
    const auto &catalog = defaultInstructionCatalog();
    const NativeInstructionSpec *spec = catalog.lookup(inst.op);
    CanonicalInstruction result;
    result.instructionId = nodeId("instruction");
    result.opcode = catalog.canonicalOpcode(inst.op);
    result.form = inst.form.empty() ? input_.defaultDtype : inst.form;
    result.opcode = catalog.specializeOpcode(result.opcode, result.form);
    spec = catalog.lookup(result.opcode);
    if (spec != nullptr && !spec->virtualOpcode &&
        !spec->forms.count(result.form) &&
        !spec->specializations.count(result.form))
      throw std::runtime_error("legacy instruction form is not canonical: " +
                               result.opcode + "." + result.form);
    result.instructionClass =
        spec == nullptr ? CanonicalInstructionClass::Compute
                        : canonicalClass(spec->instructionClass);

    const auto expectedInputs =
        trackedOperands(spec, CatalogOperandDirection::Input);
    for (size_t index = 0; index < inst.src.size(); ++index) {
      const std::string &logicalId = inst.src[index];
      const ValueInfo &logical = logicalValue(logicalId);
      CanonicalOperandRole role =
          logical.storage == ValueStorageKind::UB
              ? CanonicalOperandRole::Memory
              : logical.storage == ValueStorageKind::Scalar
                    ? CanonicalOperandRole::Scalar
                    : CanonicalOperandRole::Source;
      if (index < expectedInputs.size())
        role = canonicalRole(expectedInputs[index]->role);
      result.inputs.push_back(
          operand(ensureEntry(logicalId, environment), logicalId, role, false));
    }
    for (size_t index = inst.src.size(); index < expectedInputs.size(); ++index) {
      if (expectedInputs[index]->kind != CatalogArgumentKind::Scalar)
        break;
      const std::string logicalId = "__legacy_omitted_scalar_" +
                                    std::to_string(omittedScalarCounter_++);
      ValueInfo placeholder;
      placeholder.valueId = logicalId;
      placeholder.storage = ValueStorageKind::Scalar;
      placeholder.dtype = inst.src.empty()
                              ? input_.defaultDtype
                              : logicalValue(inst.src.front()).dtype;
      if (placeholder.dtype.empty())
        placeholder.dtype = input_.defaultDtype;
      input_.values.emplace(logicalId, std::move(placeholder));
      result.inputs.push_back(operand(ensureEntry(logicalId, environment),
                                      logicalId, CanonicalOperandRole::Scalar,
                                      false));
    }

    const auto expectedOutputs =
        trackedOperands(spec, CatalogOperandDirection::Output);
    for (size_t index = 0; index < inst.dst.size(); ++index) {
      const std::string &logicalId = inst.dst[index];
      const ValueInfo &logical = logicalValue(logicalId);
      CanonicalOperandRole role =
          logical.storage == ValueStorageKind::UB
              ? CanonicalOperandRole::Memory
              : CanonicalOperandRole::Destination;
      if (index < expectedOutputs.size())
        role = canonicalRole(expectedOutputs[index]->role);
      const std::string definitionId =
          newDefinition(logicalId, result.instructionId);
      result.outputs.push_back(operand(definitionId, logicalId, role, true));
      if (logical.storage != ValueStorageKind::UB)
        environment[logicalId] = definitionId;
    }
    return result;
  }

  void collectWrittenRegisters(const std::vector<ProgramNode> &nodes,
                               std::unordered_set<std::string> &written) const {
    for (const auto &node : nodes) {
      if (node.kind == ProgramNode::Kind::Inst) {
        for (const auto &logicalId : node.inst.dst)
          if (logicalValue(logicalId).storage == ValueStorageKind::Register)
            written.insert(logicalId);
      } else if (node.kind == ProgramNode::Kind::Loop && node.loop) {
        collectWrittenRegisters(node.loop->body, written);
      }
    }
  }

  int64_t resolveCount(const std::string &value) const {
    auto parameter = input_.params.find(value);
    if (parameter != input_.params.end())
      return parameter->second;
    try {
      return std::stoll(value);
    } catch (...) {
      return 1;
    }
  }

  CanonicalIntegerExpression integerExpression(const std::string &value) const {
    if (input_.params.count(value))
      return value;
    try {
      size_t consumed = 0;
      const int64_t parsed = std::stoll(value, &consumed);
      if (consumed == value.size())
        return parsed;
    } catch (...) {
    }
    return value;
  }

  CanonicalLoop versionLoop(const ProgramLoopNode &loop,
                            Environment &environment) {
    CanonicalLoop result;
    result.loopId = nodeId("loop", loop.name);
    result.induction.variableId = "iter_" + safeId(result.loopId);
    result.count = integerExpression(loop.iters);
    result.unroll = integerExpression(loop.unroll);

    std::unordered_set<std::string> writtenSet;
    collectWrittenRegisters(loop.body, writtenSet);
    std::vector<std::string> written(writtenSet.begin(), writtenSet.end());
    std::sort(written.begin(), written.end());
    Environment entryEnvironment = environment;
    std::unordered_map<std::string, std::string> entries;
    for (const auto &logicalId : written)
      entries.emplace(logicalId, ensureEntry(logicalId, entryEnvironment));

    Environment bodyEnvironment = entryEnvironment;
    result.body = versionNodes(loop.body, bodyEnvironment);
    Environment next = environment;
    std::unordered_map<std::string, std::string> exits;
    for (const auto &logicalId : written) {
      const std::string backEdge = bodyEnvironment.at(logicalId);
      const std::string exit = newDefinition(logicalId, result.loopId);
      result.carriedValues.push_back(
          {logicalId, entries.at(logicalId), backEdge, exit});
      exits.emplace(backEdge, exit);
      next[logicalId] = exit;
    }
    if (resolveCount(loop.iters) != 0) {
      for (const auto &[logicalId, definitionId] : bodyEnvironment) {
        if (writtenSet.count(logicalId))
          continue;
        const auto exit = exits.find(definitionId);
        next[logicalId] = exit == exits.end() ? definitionId : exit->second;
      }
    }
    environment = std::move(next);
    return result;
  }

  std::vector<CanonicalNode> versionNodes(const std::vector<ProgramNode> &nodes,
                                          Environment &environment) {
    std::vector<CanonicalNode> result;
    result.reserve(nodes.size());
    for (const auto &node : nodes) {
      if (node.kind == ProgramNode::Kind::Inst) {
        result.push_back(CanonicalNode::makeInstruction(
            versionInstruction(node.inst, environment)));
      } else if (node.kind == ProgramNode::Kind::Loop) {
        if (!node.loop)
          throw std::runtime_error("legacy loop node has no payload");
        result.push_back(
            CanonicalNode::makeLoop(versionLoop(*node.loop, environment)));
      } else {
        CanonicalMembar membar;
        membar.instructionId = nodeId("membar");
        membar.barrier = node.membar.barrier;
        result.push_back(CanonicalNode::makeMembar(std::move(membar)));
      }
    }
    return result;
  }
};

} // namespace

CanonicalVfInfo adaptLegacyVfInfoToCanonical(const VfInfo &input) {
  VfInfo normalized = input;
  canonicalizeVfInfo(normalized);
  return Adapter(std::move(normalized)).run();
}

} // namespace vfsim
