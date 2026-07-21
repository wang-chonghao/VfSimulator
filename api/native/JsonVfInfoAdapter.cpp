// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/JsonVfInfoAdapter.h"

#include "native/Json.h"

#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

const json::Value *findKey(const json::Value::Object &object,
                           const std::string &key) {
  const auto it = object.find(key);
  return it == object.end() ? nullptr : &it->second;
}

std::string scalarText(const json::Value &value) {
  return value.isString() ? value.asString() : std::to_string(value.asInt());
}

std::vector<std::string> parseStringArray(const json::Value &value,
                                          const std::string &field) {
  if (!value.isArray())
    throw std::runtime_error(field + " must be an array");
  std::vector<std::string> result;
  for (const auto &item : value.asArray())
    result.push_back(item.asString());
  return result;
}

std::vector<ProgramNode> parseNodes(const json::Value &value);

ProgramNode parseNode(const json::Value &value) {
  if (!value.isObject())
    throw std::runtime_error("program node must be an object");
  const auto &object = value.asObject();
  const json::Value *type = findKey(object, "type");
  const std::string kind = type ? type->asString() : "";
  if (kind == "inst") {
    const json::Value *op = findKey(object, "op");
    if (!op)
      throw std::runtime_error("inst node missing op");
    ProgramInstNode inst;
    inst.op = op->asString();
    if (const auto *src = findKey(object, "src"))
      inst.src = parseStringArray(*src, "src");
    if (const auto *dst = findKey(object, "dst"))
      inst.dst = parseStringArray(*dst, "dst");
    if (const auto *form = findKey(object, "form"))
      inst.form = form->asString();
    return ProgramNode::makeInst(std::move(inst));
  }
  if (kind == "loop") {
    const json::Value *iters = findKey(object, "iters");
    const json::Value *body = findKey(object, "body");
    if (!iters || !body)
      throw std::runtime_error("loop node requires iters and body");
    ProgramLoopNode loop;
    loop.iters = scalarText(*iters);
    if (const auto *unroll = findKey(object, "unroll"))
      loop.unroll = scalarText(*unroll);
    if (const auto *name = findKey(object, "name"))
      loop.name = name->asString();
    loop.body = parseNodes(*body);
    return ProgramNode::makeLoop(std::move(loop));
  }
  throw std::runtime_error("unsupported program node type: " + kind);
}

std::vector<ProgramNode> parseNodes(const json::Value &value) {
  if (!value.isArray())
    throw std::runtime_error("program must be an array");
  std::vector<ProgramNode> nodes;
  for (const auto &node : value.asArray())
    nodes.push_back(parseNode(node));
  return nodes;
}

ValueStorageKind parseStorage(const std::string &storage) {
  if (storage == "Register")
    return ValueStorageKind::Register;
  if (storage == "UB")
    return ValueStorageKind::UB;
  if (storage == "Scalar")
    return ValueStorageKind::Scalar;
  throw std::runtime_error("unsupported value storage: " + storage);
}

ValueInfo parseValue(const std::string &mapKey,
                     const json::Value::Object &object) {
  ValueInfo value;
  const auto *valueId = findKey(object, "value_id");
  value.valueId = valueId ? valueId->asString() : mapKey;
  const auto *storage = findKey(object, "storage");
  if (!storage)
    storage = findKey(object, "location");
  value.storage = storage ? parseStorage(storage->asString())
                          : inferValueStorage(value.valueId);
  if (const auto *dtype = findKey(object, "dtype"))
    value.dtype = dtype->asString();
  if (const auto *shape = findKey(object, "shape")) {
    if (!shape->isArray())
      throw std::runtime_error("value shape must be an array");
    for (const auto &dim : shape->asArray())
      value.shape.push_back(dim.asInt());
  }
  return value;
}

void parseValues(const json::Value &raw, VfInfo &vfInfo) {
  if (raw.isObject()) {
    for (const auto &[key, value] : raw.asObject()) {
      if (!value.isObject())
        throw std::runtime_error("value entry must be an object: " + key);
      ValueInfo parsed = parseValue(key, value.asObject());
      vfInfo.values[parsed.valueId] = std::move(parsed);
    }
    return;
  }
  if (raw.isArray()) {
    for (const auto &entry : raw.asArray()) {
      if (!entry.isObject())
        throw std::runtime_error("value entry must be an object");
      ValueInfo value = parseValue("", entry.asObject());
      if (value.valueId.empty())
        throw std::runtime_error("value entry missing value_id");
      vfInfo.values[value.valueId] = std::move(value);
    }
    return;
  }
  throw std::runtime_error("values must be an object or array");
}

} // namespace

VfInfo loadJsonVfInfo(const std::filesystem::path &path) {
  const json::Value root = json::parseFile(path);
  if (!root.isObject())
    throw std::runtime_error("JSON trace root must be an object");
  const auto &object = root.asObject();
  const json::Value *program = findKey(object, "program");
  if (!program)
    throw std::runtime_error("trace missing program");

  VfInfo vfInfo;
  if (const auto *dtype = findKey(object, "dtype"))
    vfInfo.defaultDtype = dtype->asString("fp32");
  if (const auto *params = findKey(object, "params")) {
    if (!params->isNull()) {
      for (const auto &[name, value] : params->asObject())
        vfInfo.params[name] = value.asInt();
    }
  }
  if (const auto *values = findKey(object, "values"))
    parseValues(*values, vfInfo);
  vfInfo.body = parseNodes(*program);
  canonicalizeVfInfo(vfInfo);
  return vfInfo;
}

} // namespace vfsim
