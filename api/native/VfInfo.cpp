// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/VfInfo.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

std::string lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

std::pair<std::string, std::string>
conversionDtypes(const std::string &form) {
  const std::size_t pos = form.find("_to_");
  if (pos == std::string::npos)
    return {"", ""};
  auto normalize = [](const std::string &dtype) {
    if (dtype == "f32")
      return std::string("fp32");
    if (dtype == "f16")
      return std::string("fp16");
    if (dtype == "s32")
      return std::string("int32");
    if (dtype == "u32")
      return std::string("uint32");
    return dtype;
  };
  return {normalize(form.substr(0, pos)),
          normalize(form.substr(pos + 4))};
}

void registerValue(VfInfo &vfInfo, const std::string &valueId) {
  if (vfInfo.values.find(valueId) != vfInfo.values.end())
    return;
  ValueInfo value;
  value.valueId = valueId;
  value.storage = inferValueStorage(valueId);
  vfInfo.values.emplace(valueId, std::move(value));
}

void canonicalizeNodes(std::vector<ProgramNode> &nodes, VfInfo &vfInfo) {
  for (ProgramNode &node : nodes) {
    if (node.kind == ProgramNode::Kind::Loop) {
      if (!node.loop)
        throw std::runtime_error("VfInfo loop node has no body");
      canonicalizeNodes(node.loop->body, vfInfo);
      continue;
    }

    ProgramInstNode &inst = node.inst;
    for (const std::string &valueId : inst.src)
      registerValue(vfInfo, valueId);
    for (const std::string &valueId : inst.dst)
      registerValue(vfInfo, valueId);

    const auto [srcConversion, dstConversion] = conversionDtypes(inst.form);
    const std::string simpleForm =
        inst.form.find("_to_") == std::string::npos ? inst.form : "";
    for (const std::string &valueId : inst.src) {
      ValueInfo &value = vfInfo.values.at(valueId);
      if (value.dtype.empty())
        value.dtype = !srcConversion.empty()
                          ? srcConversion
                          : (!simpleForm.empty() ? simpleForm
                                                 : vfInfo.defaultDtype);
    }
    for (const std::string &valueId : inst.dst) {
      ValueInfo &value = vfInfo.values.at(valueId);
      if (value.dtype.empty())
        value.dtype = !dstConversion.empty()
                          ? dstConversion
                          : (!simpleForm.empty() ? simpleForm
                                                 : vfInfo.defaultDtype);
    }

    if (inst.form.empty()) {
      std::string srcDtype;
      std::string dstDtype;
      if (!inst.src.empty())
        srcDtype = vfInfo.values.at(inst.src.front()).dtype;
      if (!inst.dst.empty())
        dstDtype = vfInfo.values.at(inst.dst.front()).dtype;
      if (!srcDtype.empty() && !dstDtype.empty() && srcDtype != dstDtype) {
        auto compact = [](const std::string &dtype) {
          if (dtype == "fp32")
            return std::string("f32");
          if (dtype == "fp16")
            return std::string("f16");
          if (dtype == "int32")
            return std::string("s32");
          if (dtype == "uint32")
            return std::string("u32");
          return dtype;
        };
        inst.form = compact(srcDtype) + "_to_" + compact(dstDtype);
      } else if (!dstDtype.empty()) {
        inst.form = dstDtype;
      } else if (!srcDtype.empty()) {
        inst.form = srcDtype;
      } else {
        inst.form = vfInfo.defaultDtype;
      }
    }
  }
}

} // namespace

ValueStorageKind inferValueStorage(const std::string &valueId) {
  const std::string normalized = lower(valueId);
  if (normalized.rfind("mem", 0) == 0)
    return ValueStorageKind::UB;
  if (normalized.rfind("v", 0) == 0)
    return ValueStorageKind::Register;
  return ValueStorageKind::Scalar;
}

std::string valueStorageName(ValueStorageKind storage) {
  switch (storage) {
  case ValueStorageKind::Register:
    return "Register";
  case ValueStorageKind::UB:
    return "UB";
  case ValueStorageKind::Scalar:
    return "Scalar";
  }
  return "Scalar";
}

void canonicalizeVfInfo(VfInfo &vfInfo) {
  if (vfInfo.defaultDtype.empty())
    vfInfo.defaultDtype = "fp32";
  for (auto &[valueId, value] : vfInfo.values) {
    if (value.valueId.empty())
      value.valueId = valueId;
    if (value.valueId != valueId)
      throw std::runtime_error("VfInfo value map key does not match valueId: " +
                               valueId);
  }
  canonicalizeNodes(vfInfo.body, vfInfo);
}

void lowerVfInfoValueIds(VfInfo &vfInfo) {
  canonicalizeVfInfo(vfInfo);
  std::unordered_map<std::string, std::string> names;
  std::unordered_map<std::string, bool> reserved;
  int64_t nextRegister = 0;
  int64_t nextUb = 0;
  for (const auto &[valueId, value] : vfInfo.values) {
    const std::string normalized = lower(valueId);
    if ((value.storage == ValueStorageKind::Register &&
         normalized.rfind("v", 0) == 0) ||
        (value.storage == ValueStorageKind::UB &&
         normalized.rfind("mem", 0) == 0)) {
      names[valueId] = valueId;
      reserved[valueId] = true;
    }
  }
  for (const auto &[valueId, value] : vfInfo.values) {
    if (names.find(valueId) != names.end())
      continue;
    switch (value.storage) {
    case ValueStorageKind::Register: {
      std::string candidate;
      do {
        candidate = "V" + std::to_string(nextRegister++);
      } while (reserved.find(candidate) != reserved.end());
      names[valueId] = candidate;
      reserved[candidate] = true;
      break;
    }
    case ValueStorageKind::UB: {
      std::string candidate;
      do {
        candidate = "mem" + std::to_string(nextUb++);
      } while (reserved.find(candidate) != reserved.end());
      names[valueId] = candidate;
      reserved[candidate] = true;
      break;
    }
    case ValueStorageKind::Scalar:
      names[valueId] = valueId;
      break;
    }
  }

  auto rewriteNodes = [&](auto &&self, std::vector<ProgramNode> &nodes) -> void {
    for (ProgramNode &node : nodes) {
      if (node.kind == ProgramNode::Kind::Loop) {
        self(self, node.loop->body);
        continue;
      }
      for (std::string &valueId : node.inst.src)
        valueId = names.at(valueId);
      for (std::string &valueId : node.inst.dst)
        valueId = names.at(valueId);
    }
  };
  rewriteNodes(rewriteNodes, vfInfo.body);

  std::unordered_map<std::string, ValueInfo> loweredValues;
  for (auto &[valueId, value] : vfInfo.values) {
    value.valueId = names.at(valueId);
    loweredValues.emplace(value.valueId, std::move(value));
  }
  vfInfo.values = std::move(loweredValues);
}

} // namespace vfsim
