// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/UarchOverrideSchema.h"

#include <iterator>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace vfsim {
namespace {

struct GeneratedUarchField {
  const char *name;
  const char *type;
  bool supportsPython;
  bool supportsCpp;
};

#include "api/native/generated/UarchOverrideSchemaData.inc"

UarchOverrideFieldType parseType(const std::string &type) {
  if (type == "integer")
    return UarchOverrideFieldType::Integer;
  if (type == "boolean")
    return UarchOverrideFieldType::Boolean;
  if (type == "string")
    return UarchOverrideFieldType::String;
  throw std::runtime_error("Unsupported generated uarch field type: " + type);
}

const std::unordered_map<std::string, UarchOverrideFieldType> &fieldTypes() {
  static const auto fields = [] {
    std::unordered_map<std::string, UarchOverrideFieldType> result;
    for (const auto &field : kGeneratedUarchFields)
      result.emplace(field.name, parseType(field.type));
    return result;
  }();
  return fields;
}

const std::unordered_set<std::string> &deprecatedFields() {
  static const std::unordered_set<std::string> fields(
      std::begin(kGeneratedDeprecatedUarchFields),
      std::end(kGeneratedDeprecatedUarchFields));
  return fields;
}

} // namespace

std::optional<UarchOverrideFieldType>
uarchOverrideFieldType(const std::string &name) {
  const auto found = fieldTypes().find(name);
  if (found == fieldTypes().end())
    return std::nullopt;
  return found->second;
}

bool isDeprecatedUarchOverrideField(const std::string &name) {
  return deprecatedFields().count(name) != 0;
}

bool uarchOverrideFieldSupportsTarget(const std::string &name,
                                      UarchOverrideTarget target) {
  for (const auto &field : kGeneratedUarchFields) {
    if (name != field.name)
      continue;
    return target == UarchOverrideTarget::Python ? field.supportsPython
                                                  : field.supportsCpp;
  }
  return false;
}

std::set<std::string>
uarchOverrideFieldsForTarget(UarchOverrideTarget target) {
  std::set<std::string> result;
  for (const auto &field : kGeneratedUarchFields) {
    const bool supported = target == UarchOverrideTarget::Python
                               ? field.supportsPython
                               : field.supportsCpp;
    if (supported)
      result.emplace(field.name);
  }
  return result;
}

const char *uarchOverrideFieldTypeName(UarchOverrideFieldType type) {
  switch (type) {
  case UarchOverrideFieldType::Integer:
    return "integer";
  case UarchOverrideFieldType::Boolean:
    return "boolean";
  case UarchOverrideFieldType::String:
    return "string";
  }
  return "unknown";
}

} // namespace vfsim
