// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/CanonicalVfInfo.h"
#include "api/native/UarchOverrideSchema.h"
#include "api/native/InstructionCatalog.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <functional>
#include <unordered_set>
#include <utility>

namespace vfsim {
namespace {

struct NodeInfo {
  std::vector<std::string> scope;
  int64_t order = 0;
  std::string kind;
};

std::optional<int64_t> parseInt64(const std::string &text) {
  if (text.empty())
    return std::nullopt;
  int64_t value = 0;
  const char *begin = text.data();
  const char *end = begin + text.size();
  const auto result = std::from_chars(begin, end, value);
  if (result.ec != std::errc{} || result.ptr != end)
    return std::nullopt;
  return value;
}

std::optional<int64_t> resolveIntegerExpression(
    const CanonicalIntegerExpression &expression,
    const std::unordered_map<std::string, int64_t> &params) {
  if (const auto *integer = std::get_if<int64_t>(&expression))
    return *integer;
  const std::string &symbol = std::get<std::string>(expression);
  auto parameter = params.find(symbol);
  if (parameter != params.end())
    return parameter->second;
  return parseInt64(symbol);
}

bool validInputRole(CanonicalOperandRole role) {
  return role == CanonicalOperandRole::Source ||
         role == CanonicalOperandRole::Memory ||
         role == CanonicalOperandRole::Scalar ||
         role == CanonicalOperandRole::Predicate ||
         role == CanonicalOperandRole::Config;
}

bool validOutputRole(CanonicalOperandRole role) {
  return role == CanonicalOperandRole::Destination ||
         role == CanonicalOperandRole::Memory;
}

bool validInstructionClass(CanonicalInstructionClass value) {
  return value == CanonicalInstructionClass::Load ||
         value == CanonicalInstructionClass::Store ||
         value == CanonicalInstructionClass::Compute ||
         value == CanonicalInstructionClass::Control;
}

bool validStorageKind(CanonicalStorageKind value) {
  return value == CanonicalStorageKind::Register ||
         value == CanonicalStorageKind::UB ||
         value == CanonicalStorageKind::Scalar;
}

CatalogInstructionClass catalogClass(CanonicalInstructionClass value) {
  switch (value) {
  case CanonicalInstructionClass::Load:
    return CatalogInstructionClass::Load;
  case CanonicalInstructionClass::Store:
    return CatalogInstructionClass::Store;
  case CanonicalInstructionClass::Compute:
    return CatalogInstructionClass::Compute;
  case CanonicalInstructionClass::Control:
    return CatalogInstructionClass::Control;
  case CanonicalInstructionClass::Unknown:
    return CatalogInstructionClass::Compute;
  }
  return CatalogInstructionClass::Compute;
}

std::string operandRoleName(CanonicalOperandRole role) {
  switch (role) {
  case CanonicalOperandRole::Source:
    return "source";
  case CanonicalOperandRole::Destination:
    return "destination";
  case CanonicalOperandRole::Memory:
    return "memory";
  case CanonicalOperandRole::Scalar:
    return "scalar";
  case CanonicalOperandRole::Predicate:
    return "predicate";
  case CanonicalOperandRole::Config:
    return "config";
  case CanonicalOperandRole::Unknown:
    return "unknown";
  }
  return "unknown";
}

bool scopePrefix(const std::vector<std::string> &prefix,
                 const std::vector<std::string> &scope) {
  return prefix.size() <= scope.size() &&
         std::equal(prefix.begin(), prefix.end(), scope.begin());
}

bool finiteScalar(const CanonicalScalar &value) {
  if (const auto *number = std::get_if<double>(&value))
    return std::isfinite(*number);
  return true;
}

const char *scalarTypeName(const CanonicalScalar &value) {
  if (std::holds_alternative<std::monostate>(value))
    return "NoneType";
  if (std::holds_alternative<bool>(value))
    return "bool";
  if (std::holds_alternative<int64_t>(value))
    return "int";
  if (std::holds_alternative<double>(value))
    return "float";
  return "str";
}

} // namespace

CanonicalNode CanonicalNode::makeInstruction(CanonicalInstruction value) {
  return CanonicalNode{std::move(value)};
}

CanonicalNode CanonicalNode::makeLoop(CanonicalLoop value) {
  return CanonicalNode{
      std::shared_ptr<const CanonicalLoop>(
          std::make_shared<CanonicalLoop>(std::move(value)))};
}

CanonicalNode CanonicalNode::makeMembar(CanonicalMembar value) {
  return CanonicalNode{std::move(value)};
}

bool CanonicalValidationResult::ok() const {
  return std::none_of(diagnostics.begin(), diagnostics.end(),
                      [](const CanonicalValidationDiagnostic &diagnostic) {
                        return diagnostic.severity == "error";
                      });
}

CanonicalValidationResult validateCanonicalVfInfo(const CanonicalVfInfo &vfInfo) {
  CanonicalValidationResult result;
  auto error = [&](std::string code, std::string message, std::string path,
                   std::optional<CanonicalSourceLocation> location = std::nullopt,
                   std::map<std::string, CanonicalScalar> context = {}) {
    result.diagnostics.push_back(CanonicalValidationDiagnostic{
        std::move(code), "error", std::move(message), std::move(path),
        std::move(location), std::move(context)});
  };

  auto validateLocation = [&](const std::optional<CanonicalSourceLocation> &location,
                              const std::string &path) {
    if (!location)
      return;
    if (location->line && *location->line <= 0)
      error("invalid_int64", "Source line must be positive", path + ".line");
    if (location->column && *location->column <= 0)
      error("invalid_int64", "Source column must be positive", path + ".column");
  };

  auto validateScalarMap = [&](const std::map<std::string, CanonicalScalar> &values,
                               const std::string &path) {
    for (const auto &[key, value] : values) {
      if (!finiteScalar(value))
        error("invalid_scalar_attribute", "Scalar must be finite",
              path + "." + key);
    }
  };

  if (vfInfo.schemaVersion != kCanonicalVfInfoSchemaVersion)
    error("unsupported_schema_version", "Unsupported schema version",
          "schema_version");
  validateScalarMap(vfInfo.uarch, "uarch");
  for (const auto &[name, value] : vfInfo.uarch) {
    if (isDeprecatedUarchOverrideField(name)) {
      error("deprecated_uarch_field",
            "Deprecated uarch field is no longer accepted", "uarch." + name);
      continue;
    }
    const auto expected = uarchOverrideFieldType(name);
    if (!expected) {
      error("unsupported_uarch_field",
            "C++ canonical frontend does not support unknown uarch field",
            "uarch." + name, std::nullopt, {{"field", name}});
      continue;
    }
    if (!uarchOverrideFieldSupportsTarget(name, UarchOverrideTarget::Cpp)) {
      error("unsupported_uarch_target",
            "uarch field is not supported by the C++ target",
            "uarch." + name, std::nullopt,
            {{"field", name}, {"target", "cpp"}});
      continue;
    }
    const bool matches =
        (*expected == UarchOverrideFieldType::Integer &&
         std::holds_alternative<int64_t>(value)) ||
        (*expected == UarchOverrideFieldType::Boolean &&
         std::holds_alternative<bool>(value)) ||
        (*expected == UarchOverrideFieldType::String &&
         std::holds_alternative<std::string>(value));
    if (!matches)
      error("uarch_field_type_mismatch",
            "uarch." + name + " must use " +
                uarchOverrideFieldTypeName(*expected) + " type",
            "uarch." + name,
            std::nullopt,
            {{"field", name},
             {"expected_type",
              std::string(uarchOverrideFieldTypeName(*expected))},
             {"actual_type", std::string(scalarTypeName(value))}});
  }
  validateScalarMap(vfInfo.source, "source");

  std::unordered_map<std::string, NodeInfo> nodeInfo;
  int64_t nextOrder = 0;
  std::function<void(const std::vector<CanonicalNode> &,
                     const std::vector<std::string> &)>
      indexNodes;
  indexNodes = [&](const std::vector<CanonicalNode> &nodes,
                   const std::vector<std::string> &scope) {
    for (const auto &node : nodes) {
      if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
        nodeInfo.emplace(inst->instructionId,
                         NodeInfo{scope, nextOrder++, "instruction"});
        continue;
      }
      if (const auto *loopPtr =
              std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload)) {
        if (!*loopPtr) {
          ++nextOrder;
          continue;
        }
        const CanonicalLoop &loop = **loopPtr;
        nodeInfo.emplace(loop.loopId, NodeInfo{scope, nextOrder++, "loop"});
        auto childScope = scope;
        childScope.push_back(loop.loopId);
        indexNodes(loop.body, childScope);
        continue;
      }
      const auto &membar = std::get<CanonicalMembar>(node.payload);
      nodeInfo.emplace(membar.instructionId,
                       NodeInfo{scope, nextOrder++, "membar"});
    }
  };
  indexNodes(vfInfo.context, {});

  for (const auto &[objectId, storageObject] : vfInfo.storageObjects) {
    const std::string path = "storage_objects." + objectId;
    if (objectId.empty() || storageObject.objectId != objectId)
      error("invalid_storage_object_identity",
            "Storage object key must match object_id", path,
            storageObject.sourceLocation);
    if (storageObject.storage != CanonicalStorageKind::UB)
      error("unsupported_storage_object_kind",
            "Canonical v1 storage objects must use UB", path,
            storageObject.sourceLocation);
    if (std::any_of(storageObject.shape.begin(), storageObject.shape.end(),
                    [](int64_t dim) { return dim < 0; }))
      error("invalid_int64", "Storage object shape must be non-negative", path,
            storageObject.sourceLocation);
    validateLocation(storageObject.sourceLocation, path + ".source_location");
  }

  for (const auto &[definitionId, value] : vfInfo.values) {
    const std::string path = "values." + definitionId;
    if (definitionId.empty() || value.definitionId != definitionId)
      error("invalid_value_identity", "Value key must match definition_id", path,
            value.sourceLocation);
    if (value.logicalId.empty())
      error("missing_logical_id", "Value must declare logical_id", path,
            value.sourceLocation);
    if (!validStorageKind(value.storage))
      error("unsupported_storage", "Value has unsupported storage", path,
            value.sourceLocation);
    if (value.dtype.empty())
      error("missing_value_dtype", "Value must declare dtype", path,
            value.sourceLocation);
    if (std::any_of(value.shape.begin(), value.shape.end(),
                    [](int64_t dim) { return dim < 0; }))
      error("invalid_int64", "Value shape must be non-negative", path,
            value.sourceLocation);
    validateLocation(value.sourceLocation, path + ".source_location");
    if (value.storage == CanonicalStorageKind::UB) {
      if (!value.storageObjectId ||
          !vfInfo.storageObjects.count(*value.storageObjectId))
        error("unknown_storage_object",
              "UB value must reference a declared storage object", path,
              value.sourceLocation);
    } else if (value.storageObjectId) {
      error("storage_object_on_non_ub_value",
            "Only UB values may reference a storage object", path,
            value.sourceLocation);
    }
  }

  std::unordered_set<std::string> registeredNodeIds;
  struct PendingDependency {
    std::string producer;
    std::string consumer;
    std::string path;
  };
  std::vector<PendingDependency> pendingDependencies;
  std::unordered_map<std::string, std::vector<std::string>> producedDefinitions;

  auto registerNodeId = [&](const std::string &nodeId, const std::string &path,
                            const std::optional<CanonicalSourceLocation> &location) {
    if (nodeId.empty() || !registeredNodeIds.insert(nodeId).second)
      error("duplicate_node_id", "Node ID must be globally unique", path,
            location);
  };

  auto producerVisibleTo = [&](const std::string &producerId,
                               const std::string &consumerId) {
    auto producer = nodeInfo.find(producerId);
    auto consumer = nodeInfo.find(consumerId);
    if (producer == nodeInfo.end() || consumer == nodeInfo.end() ||
        producer->second.order >= consumer->second.order ||
        !scopePrefix(producer->second.scope, consumer->second.scope))
      return false;
    const auto &producerInfo = producer->second;
    const auto &consumerScope = consumer->second.scope;
    if (producerInfo.kind == "loop" &&
        consumerScope.size() > producerInfo.scope.size() &&
        consumerScope[producerInfo.scope.size()] == producerId)
      return false;
    return true;
  };

  auto validateDependencies =
      [&](const std::vector<CanonicalDependencyRef> &dependencies,
          const std::string &consumer, const std::string &path) {
        for (size_t index = 0; index < dependencies.size(); ++index) {
          const auto &dependency = dependencies[index];
          const std::string itemPath = path + "[" + std::to_string(index) + "]";
          if (dependency.producerNodeId == consumer)
            error("self_dependency", "Node cannot depend on itself", itemPath);
          if (dependency.kind != CanonicalDependencyKind::Memory &&
                   dependency.kind != CanonicalDependencyKind::Control)
            error("unsupported_dependency_kind",
                  "Explicit dependency must be memory or control", itemPath);
          if (dependency.operandIndex && *dependency.operandIndex < 0)
            error("invalid_dependency_operand_index",
                  "Dependency operand_index must be non-negative", itemPath);
          auto producer = nodeInfo.find(dependency.producerNodeId);
          if (producer != nodeInfo.end() &&
              !producerVisibleTo(dependency.producerNodeId, consumer))
            error("dependency_producer_not_visible",
                  "Dependency producer is not visible before consumer", itemPath);
          pendingDependencies.push_back(
              {dependency.producerNodeId, consumer, itemPath});
        }
      };

  std::function<void(const std::vector<CanonicalNode> &, const std::string &,
                     std::unordered_set<std::string>)>
      validateNodes;
  validateNodes = [&](const std::vector<CanonicalNode> &nodes,
                      const std::string &path,
                      std::unordered_set<std::string> inductionVariables) {
    for (size_t index = 0; index < nodes.size(); ++index) {
      const CanonicalNode &node = nodes[index];
      const std::string nodePath = path + "[" + std::to_string(index) + "]";
      if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
        registerNodeId(inst->instructionId, nodePath, inst->sourceLocation);
        validateLocation(inst->sourceLocation, nodePath + ".source_location");
        if (inst->opcode.empty())
          error("missing_opcode", "Instruction must declare opcode", nodePath,
                inst->sourceLocation);
        if (!validInstructionClass(inst->instructionClass))
          error("missing_instruction_class", "Instruction class is invalid",
                nodePath, inst->sourceLocation);
        if (inst->form.empty())
          error("missing_instruction_form", "Instruction form is required",
                nodePath, inst->sourceLocation);
        const NativeInstructionSpec *catalogSpec =
            defaultInstructionCatalog().lookup(inst->opcode);
        if (catalogSpec) {
          if (inst->opcode != catalogSpec->opcode)
            error("noncanonical_opcode",
                  "Known opcode must use its canonical Catalog name", nodePath,
                  inst->sourceLocation);
          if (validInstructionClass(inst->instructionClass) &&
              catalogClass(inst->instructionClass) !=
                  catalogSpec->instructionClass)
            error("catalog_instruction_class_mismatch",
                  "Instruction class conflicts with Catalog semantics", nodePath,
                  inst->sourceLocation);
          const bool validForm = catalogSpec->virtualOpcode ||
                                 catalogSpec->forms.count(inst->form) ||
                                 catalogSpec->specializations.count(inst->form);
          if (!validForm)
            error("catalog_instruction_form_mismatch",
                  "Instruction form conflicts with Catalog semantics", nodePath,
                  inst->sourceLocation);
          auto specialized = catalogSpec->specializations.find(inst->form);
          if (specialized != catalogSpec->specializations.end() &&
              specialized->second != inst->opcode)
            error("catalog_specialization_required",
                  "Virtual opcode/form must use specialized opcode", nodePath,
                  inst->sourceLocation);
        }
        validateScalarMap(inst->attributes, nodePath + ".attributes");

        auto validateOperands = [&](const std::vector<CanonicalOperand> &operands,
                                    bool input) {
          bool hasMemory = false;
          for (size_t operandIndex = 0; operandIndex < operands.size(); ++operandIndex) {
            const auto &operand = operands[operandIndex];
            const std::string operandPath = nodePath +
                (input ? ".inputs[" : ".outputs[") +
                std::to_string(operandIndex) + "]";
            auto valueIt = vfInfo.values.find(operand.valueId);
            if (valueIt == vfInfo.values.end()) {
              error("unknown_value_reference", "Operand references unknown value",
                    operandPath, inst->sourceLocation);
              continue;
            }
            const CanonicalValue &value = valueIt->second;
            if (!(input ? validInputRole(operand.role)
                        : validOutputRole(operand.role)))
              error("operand_role_direction_mismatch",
                    "Operand role is invalid for direction", operandPath,
                    inst->sourceLocation);
            if (operand.dtype && *operand.dtype != value.dtype)
              error("operand_dtype_mismatch", "Operand dtype differs from value",
                    operandPath, inst->sourceLocation);
            const bool isUb = value.storage == CanonicalStorageKind::UB;
            if (isUb && !operand.memoryAccess)
              error("missing_memory_access", "UB operand requires memory metadata",
                    operandPath, inst->sourceLocation);
            if (!isUb && operand.memoryAccess)
              error("memory_access_on_non_ub_value",
                    "Only UB operands may carry memory metadata", operandPath,
                    inst->sourceLocation);
            if (!operand.memoryAccess)
              continue;
            const auto &memory = *operand.memoryAccess;
            if (operand.role != CanonicalOperandRole::Memory)
              error("memory_operand_role_mismatch",
                    "Memory operand must use memory role", operandPath,
                    inst->sourceLocation);
            if (!value.storageObjectId ||
                memory.baseObjectId != *value.storageObjectId)
              error("memory_base_object_mismatch",
                    "Memory base must match stable storage object", operandPath,
                    inst->sourceLocation);
            if (!vfInfo.storageObjects.count(memory.baseObjectId))
              error("unknown_memory_base_object",
                    "Memory base object is not declared", operandPath,
                    inst->sourceLocation);
            const CanonicalAccessKind expected =
                input ? CanonicalAccessKind::Read : CanonicalAccessKind::Write;
            if (memory.accessKind != expected)
              error("memory_access_direction_mismatch", "Memory direction mismatch",
                    operandPath, inst->sourceLocation);
            else
              hasMemory = true;
            if (memory.span && *memory.span <= 0)
              error("invalid_memory_span", "Memory span must be positive",
                    operandPath, inst->sourceLocation);
            std::unordered_set<std::string> affineVariables;
            for (const auto &term : memory.offset.terms) {
              if (term.variableId.empty() ||
                  !affineVariables.insert(term.variableId).second)
                error("invalid_affine_term", "Affine variables must be unique",
                      operandPath, inst->sourceLocation);
              if (!inductionVariables.count(term.variableId) &&
                  !vfInfo.params.count(term.variableId))
                error("undeclared_affine_variable",
                      "Affine variable is not in scope", operandPath,
                      inst->sourceLocation);
            }
          }
          return hasMemory;
        };

        const bool hasReadMemory = validateOperands(inst->inputs, true);
        const bool hasWriteMemory = validateOperands(inst->outputs, false);
        const bool hasInputMemory = std::any_of(
            inst->inputs.begin(), inst->inputs.end(),
            [](const CanonicalOperand &operand) {
              return operand.memoryAccess.has_value();
            });
        const bool hasOutputMemory = std::any_of(
            inst->outputs.begin(), inst->outputs.end(),
            [](const CanonicalOperand &operand) {
              return operand.memoryAccess.has_value();
            });
        for (size_t inputIndex = 0; inputIndex < inst->inputs.size(); ++inputIndex) {
          auto valueIt = vfInfo.values.find(inst->inputs[inputIndex].valueId);
          if (valueIt == vfInfo.values.end() || !valueIt->second.producerNodeId)
            continue;
          const std::string &producer = *valueIt->second.producerNodeId;
          if (producer == inst->instructionId)
            error("self_produced_input", "Input references own output",
                  nodePath + ".inputs[" + std::to_string(inputIndex) + "]");
          else if (!producerVisibleTo(producer, inst->instructionId))
            error("input_definition_not_visible",
                  "Input definition producer is not visible",
                  nodePath + ".inputs[" + std::to_string(inputIndex) + "]");
        }
        for (size_t outputIndex = 0; outputIndex < inst->outputs.size(); ++outputIndex) {
          auto valueIt = vfInfo.values.find(inst->outputs[outputIndex].valueId);
          if (valueIt != vfInfo.values.end() &&
              valueIt->second.producerNodeId != inst->instructionId)
            error("output_producer_mismatch",
                  "Output definition must name producing instruction",
                  nodePath + ".outputs[" + std::to_string(outputIndex) + "]");
          producedDefinitions[inst->instructionId].push_back(
              inst->outputs[outputIndex].valueId);
        }
        if (inst->instructionClass == CanonicalInstructionClass::Load && !hasReadMemory)
          error("load_without_memory_read", "Load requires memory read", nodePath);
        if (inst->instructionClass == CanonicalInstructionClass::Load && hasOutputMemory)
          error("instruction_class_memory_access_mismatch",
                "Load instructions cannot write memory", nodePath);
        if (inst->instructionClass == CanonicalInstructionClass::Store && !hasWriteMemory)
          error("store_without_memory_write", "Store requires memory write", nodePath);
        if (inst->instructionClass == CanonicalInstructionClass::Store && hasInputMemory)
          error("instruction_class_memory_access_mismatch",
                "Store instructions cannot read memory", nodePath);
        if ((inst->instructionClass == CanonicalInstructionClass::Compute ||
             inst->instructionClass == CanonicalInstructionClass::Control) &&
            (hasInputMemory || hasOutputMemory))
          error("instruction_class_memory_access_mismatch",
                "Compute/control instructions cannot access memory", nodePath);
        if (catalogSpec) {
          std::vector<const NativeOperandSpec *> expectedInputs;
          std::vector<const NativeOperandSpec *> expectedOutputs;
          for (const auto &operand : catalogSpec->operands) {
            if (operand.direction == CatalogOperandDirection::Input)
              expectedInputs.push_back(&operand);
            else if (operand.direction == CatalogOperandDirection::Output)
              expectedOutputs.push_back(&operand);
          }
          auto actualOperands = [](const std::vector<CanonicalOperand> &operands) {
            std::vector<const CanonicalOperand *> result;
            for (const auto &operand : operands)
              if (operand.role != CanonicalOperandRole::Predicate &&
                  operand.role != CanonicalOperandRole::Config)
                result.push_back(&operand);
            return result;
          };
          const auto actualInputs = actualOperands(inst->inputs);
          const auto actualOutputs = actualOperands(inst->outputs);
          auto validateCatalogOperands = [&](const auto &actual,
                                             const auto &expected,
                                             const std::string &direction) {
            if (actual.size() != expected.size()) {
              error("catalog_operand_count_mismatch",
                    "Operand count conflicts with Catalog signature", nodePath,
                    inst->sourceLocation);
              return;
            }
            for (size_t operandIndex = 0; operandIndex < actual.size();
                 ++operandIndex) {
              const auto &operand = *actual[operandIndex];
              const auto &operandSpec = *expected[operandIndex];
              const std::string operandPath = nodePath + "." + direction + "[" +
                                              std::to_string(operandIndex) + "]";
              if (operandRoleName(operand.role) != operandSpec.role)
                error("catalog_operand_role_mismatch",
                      "Operand role conflicts with Catalog signature", operandPath,
                      inst->sourceLocation);
              auto value = vfInfo.values.find(operand.valueId);
              if (value == vfInfo.values.end())
                continue;
              const CanonicalStorageKind storage = value->second.storage;
              bool storageMatches = true;
              switch (operandSpec.kind) {
              case CatalogArgumentKind::Register:
                storageMatches = storage == CanonicalStorageKind::Register;
                break;
              case CatalogArgumentKind::UB:
                storageMatches = storage == CanonicalStorageKind::UB;
                break;
              case CatalogArgumentKind::Scalar:
                storageMatches = storage == CanonicalStorageKind::Scalar;
                break;
              case CatalogArgumentKind::RegisterOrScalar:
                storageMatches = storage == CanonicalStorageKind::Register ||
                                 storage == CanonicalStorageKind::Scalar;
                break;
              case CatalogArgumentKind::Predicate:
              case CatalogArgumentKind::Config:
                break;
              }
              if (!storageMatches)
                error("catalog_operand_storage_mismatch",
                      "Operand storage conflicts with Catalog signature", operandPath,
                      inst->sourceLocation);
            }
          };
          validateCatalogOperands(actualInputs, expectedInputs, "inputs");
          validateCatalogOperands(actualOutputs, expectedOutputs, "outputs");
        }
        validateDependencies(inst->dependencies, inst->instructionId,
                             nodePath + ".dependencies");
        continue;
      }

      if (const auto *loopPtr =
              std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload)) {
        if (!*loopPtr) {
          error("invalid_loop_payload", "Loop payload cannot be null", nodePath);
          continue;
        }
        const CanonicalLoop &loop = **loopPtr;
        registerNodeId(loop.loopId, nodePath, loop.sourceLocation);
        validateLocation(loop.sourceLocation, nodePath + ".source_location");
        const auto count = resolveIntegerExpression(loop.count, vfInfo.params);
        const auto unroll = resolveIntegerExpression(loop.unroll, vfInfo.params);
        const auto start = resolveIntegerExpression(loop.induction.start, vfInfo.params);
        const auto step = resolveIntegerExpression(loop.induction.step, vfInfo.params);
        if (!count)
          error("unresolved_parameter", "Loop count cannot be resolved",
                nodePath + ".count");
        else if (*count < 0)
          error("invalid_loop_count", "Loop count must be non-negative", nodePath);
        if (!unroll)
          error("unresolved_parameter", "Loop unroll cannot be resolved",
                nodePath + ".unroll");
        else if (*unroll <= 0)
          error("invalid_loop_unroll", "Loop unroll must be positive", nodePath);
        if (!start)
          error("unresolved_parameter", "Induction start cannot be resolved", nodePath);
        if (!step)
          error("unresolved_parameter", "Induction step cannot be resolved", nodePath);
        else if (*step == 0)
          error("invalid_induction_step", "Induction step cannot be zero", nodePath);
        const std::string &variableId = loop.induction.variableId;
        if (variableId.empty() || inductionVariables.count(variableId) ||
            vfInfo.params.count(variableId))
          error("invalid_induction_variable", "Induction variable is invalid", nodePath);

        auto loopInfoIt = nodeInfo.find(loop.loopId);
        std::vector<std::string> loopScope;
        if (loopInfoIt != nodeInfo.end()) {
          loopScope = loopInfoIt->second.scope;
          loopScope.push_back(loop.loopId);
        }
        std::unordered_set<std::string> carriedLogicalIds;
        for (size_t carriedIndex = 0; carriedIndex < loop.carriedValues.size();
             ++carriedIndex) {
          const auto &carried = loop.carriedValues[carriedIndex];
          const std::string carriedPath = nodePath + ".carried_values[" +
                                          std::to_string(carriedIndex) + "]";
          if (carried.logicalId.empty() ||
              !carriedLogicalIds.insert(carried.logicalId).second)
            error("duplicate_loop_carried_value",
                  "Loop-carried logical_id must be unique", carriedPath);
          auto entryIt = vfInfo.values.find(carried.entryValueId);
          auto backIt = vfInfo.values.find(carried.backEdgeValueId);
          auto exitIt = vfInfo.values.find(carried.exitValueId);
          if (entryIt == vfInfo.values.end() || backIt == vfInfo.values.end() ||
              exitIt == vfInfo.values.end()) {
            error("unknown_loop_carried_value",
                  "Unknown loop-carried definition", carriedPath);
            continue;
          }
          const CanonicalValue &entry = entryIt->second;
          const CanonicalValue &back = backIt->second;
          const CanonicalValue &exit = exitIt->second;
          if (entry.logicalId != carried.logicalId ||
              exit.logicalId != carried.logicalId)
            error("loop_carried_logical_id_mismatch",
                  "Loop entry and exit logical IDs must match the carried state",
                  carriedPath);
          if (entry.storage != back.storage || entry.storage != exit.storage ||
              entry.dtype != back.dtype || entry.dtype != exit.dtype ||
              entry.shape != back.shape || entry.shape != exit.shape ||
              entry.storageObjectId != back.storageObjectId ||
              entry.storageObjectId != exit.storageObjectId)
            error("loop_carried_type_mismatch",
                  "Loop-carried value metadata must match", carriedPath);
          if (entry.producerNodeId) {
            auto producer = nodeInfo.find(*entry.producerNodeId);
            const bool visible = producer != nodeInfo.end() &&
                loopInfoIt != nodeInfo.end() &&
                producer->second.order < loopInfoIt->second.order &&
                scopePrefix(producer->second.scope, loopInfoIt->second.scope) &&
                !(producer->second.kind == "loop" &&
                  loopInfoIt->second.scope.size() > producer->second.scope.size() &&
                  loopInfoIt->second.scope[producer->second.scope.size()] ==
                      *entry.producerNodeId);
            if (!visible)
              error("loop_entry_not_visible",
                    "Loop entry is not visible before loop", carriedPath);
          }
          if (carried.backEdgeValueId != carried.entryValueId) {
            auto producer = back.producerNodeId
                ? nodeInfo.find(*back.producerNodeId)
                : nodeInfo.end();
            const bool producedInBody =
                producer != nodeInfo.end() && producer->second.scope == loopScope;
            const bool visibleBeforeLoop = !back.producerNodeId ||
                (producer != nodeInfo.end() && loopInfoIt != nodeInfo.end() &&
                 producer->second.order < loopInfoIt->second.order &&
                 scopePrefix(producer->second.scope, loopInfoIt->second.scope) &&
                 !(producer->second.kind == "loop" &&
                   loopInfoIt->second.scope.size() >
                       producer->second.scope.size() &&
                   loopInfoIt->second.scope[producer->second.scope.size()] ==
                       *back.producerNodeId));
            if (!producedInBody && !visibleBeforeLoop)
              error("loop_back_edge_out_of_scope",
                    "Back-edge definition must be visible at loop tail",
                    carriedPath);
          }
          if (exit.producerNodeId != loop.loopId)
            error("loop_exit_producer_mismatch",
                  "Loop exit must be produced by loop node", carriedPath);
          producedDefinitions[loop.loopId].push_back(carried.exitValueId);
        }
        inductionVariables.insert(variableId);
        validateNodes(loop.body, nodePath + ".body", std::move(inductionVariables));
        continue;
      }

      const auto &membar = std::get<CanonicalMembar>(node.payload);
      registerNodeId(membar.instructionId, nodePath, membar.sourceLocation);
      validateLocation(membar.sourceLocation, nodePath + ".source_location");
      if (membar.barrier.empty())
        error("missing_membar_type", "Membar type is required", nodePath,
              membar.sourceLocation);
      validateDependencies(membar.dependencies, membar.instructionId,
                           nodePath + ".dependencies");
    }
  };
  validateNodes(vfInfo.context, "context", {});

  for (const auto &[definitionId, value] : vfInfo.values) {
    if (!value.producerNodeId)
      continue;
    auto producer = nodeInfo.find(*value.producerNodeId);
    if (producer == nodeInfo.end()) {
      error("unknown_value_producer", "Value references unknown producer node",
            "values." + definitionId + ".producer_node_id", value.sourceLocation);
      continue;
    }
    if (producer->second.kind == "membar") {
      error("invalid_value_producer_kind",
            "Membar cannot produce a value definition",
            "values." + definitionId + ".producer_node_id", value.sourceLocation);
      continue;
    }
    const auto emitted = producedDefinitions.find(*value.producerNodeId);
    const int64_t count = emitted == producedDefinitions.end()
        ? 0
        : static_cast<int64_t>(
              std::count(emitted->second.begin(), emitted->second.end(), definitionId));
    if (count == 0)
      error("producer_definition_not_emitted",
            "Producer node does not emit the claimed value definition",
            "values." + definitionId + ".producer_node_id", value.sourceLocation);
    else if (count > 1)
      error("definition_emitted_multiple_times",
            "Producer node emits the same definition more than once",
            "values." + definitionId + ".producer_node_id", value.sourceLocation);
  }
  for (const auto &dependency : pendingDependencies) {
    if (!nodeInfo.count(dependency.producer))
      error("unknown_dependency_producer",
            "Dependency references unknown producer node", dependency.path);
  }
  return result;
}

} // namespace vfsim
