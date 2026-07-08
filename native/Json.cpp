// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/Json.h"

#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace vfsim::json {
namespace {

class Parser {
public:
  explicit Parser(std::string text) : text_(std::move(text)) {}

  Value parse() {
    skipWs();
    Value value = parseValue();
    skipWs();
    if (!eof())
      fail("unexpected trailing characters");
    return value;
  }

private:
  std::string text_;
  size_t pos_ = 0;

  [[noreturn]] void fail(const std::string &message) const {
    throw std::runtime_error("JSON parse error at offset " +
                             std::to_string(pos_) + ": " + message);
  }

  bool eof() const { return pos_ >= text_.size(); }

  char peek() const {
    if (eof())
      return '\0';
    return text_[pos_];
  }

  char get() {
    if (eof())
      fail("unexpected end of input");
    return text_[pos_++];
  }

  void skipWs() {
    while (!eof() && std::isspace(static_cast<unsigned char>(peek())))
      ++pos_;
  }

  bool consume(char c) {
    if (peek() != c)
      return false;
    ++pos_;
    return true;
  }

  Value parseValue() {
    skipWs();
    switch (peek()) {
    case '{':
      return parseObject();
    case '[':
      return parseArray();
    case '"':
      return Value(parseString());
    case 't':
      parseLiteral("true");
      return Value(true);
    case 'f':
      parseLiteral("false");
      return Value(false);
    case 'n':
      parseLiteral("null");
      return Value(nullptr);
    default:
      if (peek() == '-' || std::isdigit(static_cast<unsigned char>(peek())))
        return parseNumber();
      fail("unexpected token");
    }
  }

  void parseLiteral(const char *literal) {
    for (size_t i = 0; literal[i] != '\0'; ++i) {
      if (get() != literal[i])
        fail(std::string("expected literal ") + literal);
    }
  }

  Value parseObject() {
    if (!consume('{'))
      fail("expected '{'");

    Value::Object obj;
    skipWs();
    if (consume('}'))
      return Value(std::move(obj));

    while (true) {
      skipWs();
      if (peek() != '"')
        fail("expected object key");
      std::string key = parseString();
      skipWs();
      if (!consume(':'))
        fail("expected ':' after object key");
      skipWs();
      obj.emplace(std::move(key), parseValue());
      skipWs();
      if (consume('}'))
        break;
      if (!consume(','))
        fail("expected ',' or '}' in object");
    }

    return Value(std::move(obj));
  }

  Value parseArray() {
    if (!consume('['))
      fail("expected '['");

    Value::Array arr;
    skipWs();
    if (consume(']'))
      return Value(std::move(arr));

    while (true) {
      skipWs();
      arr.push_back(parseValue());
      skipWs();
      if (consume(']'))
        break;
      if (!consume(','))
        fail("expected ',' or ']' in array");
    }

    return Value(std::move(arr));
  }

  std::string parseString() {
    if (!consume('"'))
      fail("expected string");

    std::string out;
    while (true) {
      if (eof())
        fail("unterminated string");
      char c = get();
      if (c == '"')
        break;
      if (c != '\\') {
        out.push_back(c);
        continue;
      }
      if (eof())
        fail("unterminated escape sequence");
      char e = get();
      switch (e) {
      case '"':
        out.push_back('"');
        break;
      case '\\':
        out.push_back('\\');
        break;
      case '/':
        out.push_back('/');
        break;
      case 'b':
        out.push_back('\b');
        break;
      case 'f':
        out.push_back('\f');
        break;
      case 'n':
        out.push_back('\n');
        break;
      case 'r':
        out.push_back('\r');
        break;
      case 't':
        out.push_back('\t');
        break;
      case 'u':
        fail("unicode escapes are not supported in native config JSON");
      default:
        fail("invalid escape sequence");
      }
    }
    return out;
  }

  Value parseNumber() {
    const size_t start = pos_;
    if (peek() == '-')
      ++pos_;
    while (!eof() && std::isdigit(static_cast<unsigned char>(peek())))
      ++pos_;
    bool isFloat = false;
    if (!eof() && peek() == '.') {
      isFloat = true;
      ++pos_;
      while (!eof() && std::isdigit(static_cast<unsigned char>(peek())))
        ++pos_;
    }
    if (!eof() && (peek() == 'e' || peek() == 'E')) {
      isFloat = true;
      ++pos_;
      if (peek() == '+' || peek() == '-')
        ++pos_;
      while (!eof() && std::isdigit(static_cast<unsigned char>(peek())))
        ++pos_;
    }

    const std::string token = text_.substr(start, pos_ - start);
    if (isFloat)
      return Value(std::stod(token));
    return Value(static_cast<int64_t>(std::stoll(token)));
  }
};

} // namespace

bool Value::asBool(bool defaultValue) const {
  if (kind == Kind::Bool)
    return std::get<bool>(data);
  return defaultValue;
}

int64_t Value::asInt(int64_t defaultValue) const {
  if (kind == Kind::Int)
    return std::get<int64_t>(data);
  if (kind == Kind::Double)
    return static_cast<int64_t>(std::get<double>(data));
  if (kind == Kind::Bool)
    return std::get<bool>(data) ? 1 : 0;
  return defaultValue;
}

double Value::asDouble(double defaultValue) const {
  if (kind == Kind::Double)
    return std::get<double>(data);
  if (kind == Kind::Int)
    return static_cast<double>(std::get<int64_t>(data));
  if (kind == Kind::Bool)
    return std::get<bool>(data) ? 1.0 : 0.0;
  return defaultValue;
}

std::string Value::asString(const std::string &defaultValue) const {
  if (kind == Kind::String)
    return std::get<std::string>(data);
  return defaultValue;
}

const Value::Object &Value::asObject() const {
  if (kind != Kind::Object)
    throw std::runtime_error("JSON value is not an object");
  return *std::get<std::shared_ptr<Object>>(data);
}

const Value::Array &Value::asArray() const {
  if (kind != Kind::Array)
    throw std::runtime_error("JSON value is not an array");
  return *std::get<std::shared_ptr<Array>>(data);
}

Value parseString(const std::string &text) {
  return Parser(text).parse();
}

Value parseFile(const std::filesystem::path &path) {
  std::ifstream in(path);
  if (!in.is_open())
    throw std::runtime_error("failed to open JSON file: " + path.string());
  std::ostringstream buffer;
  buffer << in.rdbuf();
  return parseString(buffer.str());
}

} // namespace vfsim::json
