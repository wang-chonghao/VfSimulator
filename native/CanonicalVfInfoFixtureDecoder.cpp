// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/CanonicalVfInfoFixtureDecoder.h"

#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

using Object = json::Value::Object;

const json::Value *find(const Object &object, const std::string &key) {
  auto it = object.find(key);
  return it == object.end() ? nullptr : &it->second;
}

const json::Value &required(const Object &object, const std::string &key) {
  const json::Value *value = find(object, key);
  if (!value)
    throw std::runtime_error("canonical fixture missing field: " + key);
  return *value;
}

std::optional<std::string> optionalString(const Object &object,
                                          const std::string &key) {
  const json::Value *value = find(object, key);
  if (!value || value->isNull())
    return std::nullopt;
  if (!value->isString())
    throw std::runtime_error("canonical fixture field must be string: " + key);
  return value->asString();
}

std::optional<int64_t> optionalInt(const Object &object,
                                   const std::string &key) {
  const json::Value *value = find(object, key);
  if (!value || value->isNull())
    return std::nullopt;
  if (!value->isInt())
    throw std::runtime_error("canonical fixture field must be int: " + key);
  return value->asInt();
}

CanonicalScalar scalar(const json::Value &value) {
  if (value.isNull())
    return std::monostate{};
  if (value.isBool())
    return value.asBool();
  if (value.isInt())
    return value.asInt();
  if (value.isDouble())
    return value.asDouble();
  if (value.isString())
    return value.asString();
  throw std::runtime_error("canonical fixture scalar cannot be object/array");
}

std::map<std::string, CanonicalScalar> scalarMap(const json::Value *value) {
  std::map<std::string, CanonicalScalar> result;
  if (!value)
    return result;
  for (const auto &[key, item] : value->asObject())
    result.emplace(key, scalar(item));
  return result;
}

std::vector<int64_t> shape(const json::Value *value) {
  std::vector<int64_t> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray()) {
    if (!item.isInt())
      throw std::runtime_error("canonical fixture shape must contain ints");
    result.push_back(item.asInt());
  }
  return result;
}

std::optional<CanonicalSourceLocation> sourceLocation(const json::Value *value) {
  if (!value || value->isNull())
    return std::nullopt;
  const auto &object = value->asObject();
  CanonicalSourceLocation result;
  result.source = optionalString(object, "source");
  result.line = optionalInt(object, "line");
  result.column = optionalInt(object, "column");
  result.path = optionalString(object, "path");
  return result;
}

CanonicalStorageKind storageKind(const std::string &value) {
  if (value == "Register")
    return CanonicalStorageKind::Register;
  if (value == "UB")
    return CanonicalStorageKind::UB;
  if (value == "Scalar")
    return CanonicalStorageKind::Scalar;
  throw std::runtime_error("unsupported canonical storage: " + value);
}

CanonicalInstructionClass instructionClass(const std::string &value) {
  if (value == "load")
    return CanonicalInstructionClass::Load;
  if (value == "store")
    return CanonicalInstructionClass::Store;
  if (value == "compute")
    return CanonicalInstructionClass::Compute;
  if (value == "control")
    return CanonicalInstructionClass::Control;
  throw std::runtime_error("unsupported canonical instruction class: " + value);
}

CanonicalOperandRole operandRole(const std::string &value) {
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
  throw std::runtime_error("unsupported canonical operand role: " + value);
}

CanonicalDependencyKind dependencyKind(const std::string &value) {
  if (value == "memory")
    return CanonicalDependencyKind::Memory;
  if (value == "control")
    return CanonicalDependencyKind::Control;
  throw std::runtime_error("unsupported canonical dependency kind: " + value);
}

CanonicalIntegerExpression integerExpression(const json::Value &value) {
  if (value.isInt())
    return value.asInt();
  if (value.isString())
    return value.asString();
  throw std::runtime_error("canonical integer expression must be int/string");
}

CanonicalDependencyRef dependency(const json::Value &value) {
  const auto &object = value.asObject();
  CanonicalDependencyRef result;
  result.producerNodeId = required(object, "producer_node_id").asString();
  result.kind = dependencyKind(required(object, "kind").asString());
  result.operandIndex = optionalInt(object, "operand_index");
  return result;
}

std::vector<CanonicalDependencyRef> dependencies(const json::Value *value) {
  std::vector<CanonicalDependencyRef> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray())
    result.push_back(dependency(item));
  return result;
}

CanonicalOperand operand(const json::Value &value) {
  const auto &object = value.asObject();
  CanonicalOperand result;
  result.valueId = required(object, "value_id").asString();
  result.role = operandRole(required(object, "role").asString());
  result.dtype = optionalString(object, "dtype");
  if (const json::Value *memoryValue = find(object, "memory_access")) {
    if (!memoryValue->isNull()) {
      const auto &memoryObject = memoryValue->asObject();
      CanonicalMemoryAccess memory;
      memory.baseObjectId = required(memoryObject, "base_object_id").asString();
      const auto &offsetObject = required(memoryObject, "offset").asObject();
      memory.offset.constant = required(offsetObject, "constant").asInt();
      for (const auto &termValue : required(offsetObject, "terms").asArray()) {
        const auto &termObject = termValue.asObject();
        memory.offset.terms.push_back(CanonicalAffineTerm{
            required(termObject, "variable_id").asString(),
            required(termObject, "coefficient").asInt()});
      }
      const std::string access = required(memoryObject, "access_kind").asString();
      if (access == "read")
        memory.accessKind = CanonicalAccessKind::Read;
      else if (access == "write")
        memory.accessKind = CanonicalAccessKind::Write;
      else
        throw std::runtime_error("unsupported canonical access kind: " + access);
      memory.span = optionalInt(memoryObject, "span");
      memory.aliasGroup = optionalString(memoryObject, "alias_group");
      result.memoryAccess = std::move(memory);
    }
  }
  return result;
}

std::vector<CanonicalOperand> operands(const json::Value *value) {
  std::vector<CanonicalOperand> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray())
    result.push_back(operand(item));
  return result;
}

CanonicalNode node(const json::Value &value) {
  const auto &object = value.asObject();
  const std::string kind = required(object, "kind").asString();
  if (kind == "instruction") {
    CanonicalInstruction result;
    result.instructionId = required(object, "instruction_id").asString();
    result.opcode = required(object, "opcode").asString();
    result.instructionClass =
        instructionClass(required(object, "instruction_class").asString());
    result.form = required(object, "form").asString();
    result.inputs = operands(find(object, "inputs"));
    result.outputs = operands(find(object, "outputs"));
    result.dependencies = dependencies(find(object, "dependencies"));
    result.attributes = scalarMap(find(object, "attributes"));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeInstruction(std::move(result));
  }
  if (kind == "loop") {
    CanonicalLoop result;
    result.loopId = required(object, "loop_id").asString();
    const auto &inductionObject = required(object, "induction").asObject();
    result.induction.variableId =
        required(inductionObject, "variable_id").asString();
    result.induction.start = integerExpression(required(inductionObject, "start"));
    result.induction.step = integerExpression(required(inductionObject, "step"));
    result.count = integerExpression(required(object, "count"));
    result.unroll = integerExpression(required(object, "unroll"));
    if (const json::Value *carriedValues = find(object, "carried_values")) {
      for (const auto &item : carriedValues->asArray()) {
        const auto &carried = item.asObject();
        result.carriedValues.push_back(CanonicalLoopCarriedValue{
            required(carried, "logical_id").asString(),
            required(carried, "entry_value_id").asString(),
            required(carried, "back_edge_value_id").asString(),
            required(carried, "exit_value_id").asString()});
      }
    }
    for (const auto &item : required(object, "body").asArray())
      result.body.push_back(node(item));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeLoop(std::move(result));
  }
  if (kind == "membar") {
    CanonicalMembar result;
    result.instructionId = required(object, "instruction_id").asString();
    result.barrier = required(object, "barrier").asString();
    result.dependencies = dependencies(find(object, "dependencies"));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeMembar(std::move(result));
  }
  throw std::runtime_error("unsupported canonical node kind: " + kind);
}

} // namespace

CanonicalVfInfo decodeCanonicalVfInfoFixture(const json::Value &rootValue) {
  const auto &root = rootValue.asObject();
  CanonicalVfInfo result;
  result.schemaVersion = required(root, "schema_version").asInt();
  for (const auto &item : required(root, "context").asArray())
    result.context.push_back(node(item));
  for (const auto &[definitionId, value] : required(root, "values").asObject()) {
    const auto &object = value.asObject();
    CanonicalValue decoded;
    decoded.definitionId = required(object, "definition_id").asString();
    decoded.logicalId = required(object, "logical_id").asString();
    decoded.storage = storageKind(required(object, "storage").asString());
    decoded.dtype = required(object, "dtype").asString();
    decoded.shape = shape(find(object, "shape"));
    decoded.producerNodeId = optionalString(object, "producer_node_id");
    decoded.storageObjectId = optionalString(object, "storage_object_id");
    decoded.sourceLocation = sourceLocation(find(object, "source_location"));
    result.values.emplace(definitionId, std::move(decoded));
  }
  if (const json::Value *storageObjects = find(root, "storage_objects")) {
    for (const auto &[objectId, value] : storageObjects->asObject()) {
      const auto &object = value.asObject();
      CanonicalStorageObject decoded;
      decoded.objectId = required(object, "object_id").asString();
      decoded.storage = storageKind(required(object, "storage").asString());
      decoded.shape = shape(find(object, "shape"));
      decoded.sourceLocation = sourceLocation(find(object, "source_location"));
      result.storageObjects.emplace(objectId, std::move(decoded));
    }
  }
  if (const json::Value *params = find(root, "params"))
    for (const auto &[key, value] : params->asObject())
      result.params.emplace(key, value.asInt());
  result.uarch = scalarMap(find(root, "uarch"));
  result.source = scalarMap(find(root, "source"));
  return result;
}

} // namespace vfsim
