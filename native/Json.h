// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_JSON_H
#define VFSIM_NATIVE_JSON_H

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace vfsim::json {

struct Value {
  enum class Kind { Null, Bool, Int, Double, String, Object, Array };
  using Object = std::unordered_map<std::string, Value>;
  using Array = std::vector<Value>;

  Kind kind = Kind::Null;
  std::variant<std::monostate, bool, int64_t, double, std::string,
               std::shared_ptr<Object>, std::shared_ptr<Array>>
      data;

  Value() = default;
  explicit Value(std::nullptr_t) : kind(Kind::Null), data(std::monostate{}) {}
  explicit Value(bool v) : kind(Kind::Bool), data(v) {}
  explicit Value(int64_t v) : kind(Kind::Int), data(v) {}
  explicit Value(double v) : kind(Kind::Double), data(v) {}
  explicit Value(std::string v) : kind(Kind::String), data(std::move(v)) {}
  explicit Value(Object v)
      : kind(Kind::Object),
        data(std::make_shared<Object>(std::move(v))) {}
  explicit Value(Array v)
      : kind(Kind::Array), data(std::make_shared<Array>(std::move(v))) {}

  bool isNull() const { return kind == Kind::Null; }
  bool isBool() const { return kind == Kind::Bool; }
  bool isInt() const { return kind == Kind::Int; }
  bool isDouble() const { return kind == Kind::Double; }
  bool isString() const { return kind == Kind::String; }
  bool isObject() const { return kind == Kind::Object; }
  bool isArray() const { return kind == Kind::Array; }

  bool asBool(bool defaultValue = false) const;
  int64_t asInt(int64_t defaultValue = 0) const;
  double asDouble(double defaultValue = 0.0) const;
  std::string asString(const std::string &defaultValue = "") const;
  const Object &asObject() const;
  const Array &asArray() const;
};

Value parseFile(const std::filesystem::path &path);
Value parseString(const std::string &text);

} // namespace vfsim::json

#endif // VFSIM_NATIVE_JSON_H
