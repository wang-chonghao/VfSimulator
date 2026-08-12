// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/VfInfo.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <unordered_map>
#include <utility>

namespace vfsim {
namespace {

std::string lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

std::string upper(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return value;
}

std::string normalizeDtype(const std::string &dtype) {
  const std::string text = lower(dtype);
  static const std::unordered_map<std::string, std::string> aliases = {
      {"f32", "fp32"},       {"float32", "fp32"},   {"fp32", "fp32"},
      {"f16", "fp16"},       {"float16", "fp16"},   {"fp16", "fp16"},
      {"bf16", "bf16"},      {"bfloat16", "bf16"},  {"s32", "int32"},
      {"i32", "int32"},      {"int32", "int32"},    {"u32", "uint32"},
      {"uint32", "uint32"},  {"bool", "bool"},      {"boolean", "bool"},
      {"predicate", "bool"},
  };
  const auto it = aliases.find(text);
  return it == aliases.end() ? text : it->second;
}

std::string compactDtype(const std::string &dtype) {
  const std::string normalized = normalizeDtype(dtype);
  if (normalized == "fp32")
    return "f32";
  if (normalized == "fp16")
    return "f16";
  if (normalized == "int32")
    return "s32";
  if (normalized == "uint32")
    return "u32";
  return normalized;
}

std::string normalizeForm(const std::string &form) {
  const std::string text = lower(form);
  const std::size_t pos = text.find("_to_");
  if (pos == std::string::npos)
    return normalizeDtype(text);
  return compactDtype(text.substr(0, pos)) + "_to_" +
         compactDtype(text.substr(pos + 4));
}

std::string normalizeOpcode(const std::string &op) {
  const std::string text = lower(op);
  static const std::unordered_map<std::string, std::string> aliases = {
      {"vld", "VLDS"},
      {"vlds", "VLDS"},
      {"vst", "VSTS"},
      {"vsts", "VSTS"},
      {"vstus", "VSTUS"},
      {"vstas", "VSTAS"},
      {"vadd", "VADD"},
      {"vadds", "VADDS"},
      {"vmul", "VMUL"},
      {"vmuls", "VMULS"},
      {"vcvt", "VCVT"},
      {"vcvt_f32_to_f16", "VCVT_F32_TO_F16"},
      {"vcvt_f16_to_f32", "VCVT_F16_TO_F32"},
      {"vcvt_f32_to_s32", "VCVT_F32_TO_S32"},
      {"vcvt_s32_to_f32", "VCVT_S32_TO_F32"},
  };
  const auto it = aliases.find(text);
  return it == aliases.end() ? upper(op) : it->second;
}

std::string specializeOpcode(const std::string &op, const std::string &form) {
  const std::string canonicalOp = normalizeOpcode(op);
  if (canonicalOp != "VCVT")
    return canonicalOp;
  const std::string canonicalForm = normalizeForm(form);
  if (canonicalForm == "f32_to_f16")
    return "VCVT_F32_TO_F16";
  if (canonicalForm == "f16_to_f32")
    return "VCVT_F16_TO_F32";
  if (canonicalForm == "f32_to_s32")
    return "VCVT_F32_TO_S32";
  if (canonicalForm == "s32_to_f32")
    return "VCVT_S32_TO_F32";
  return canonicalOp;
}

std::string normalizeMembarType(const std::string &barrier) {
  std::string text = upper(barrier.empty() ? "VST_VLD" : barrier);
  const std::size_t dot = text.rfind('.');
  if (dot != std::string::npos)
    text = text.substr(dot + 1);
  if (text == "VST_VLD" || text == "VLD_VST")
    return text;
  return text;
}

std::pair<std::string, std::string>
conversionDtypes(const std::string &form) {
  const std::string normalizedForm = normalizeForm(form);
  const std::size_t pos = normalizedForm.find("_to_");
  if (pos == std::string::npos)
    return {"", ""};
  return {normalizeDtype(normalizedForm.substr(0, pos)),
          normalizeDtype(normalizedForm.substr(pos + 4))};
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
    if (node.kind == ProgramNode::Kind::Membar) {
      node.membar.barrier = normalizeMembarType(node.membar.barrier);
      continue;
    }

    ProgramInstNode &inst = node.inst;
    inst.op = normalizeOpcode(inst.op);
    inst.form = normalizeForm(inst.form);
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
        inst.form = compactDtype(srcDtype) + "_to_" + compactDtype(dstDtype);
      } else if (!dstDtype.empty()) {
        inst.form = normalizeDtype(dstDtype);
      } else if (!srcDtype.empty()) {
        inst.form = normalizeDtype(srcDtype);
      } else {
        inst.form = normalizeDtype(vfInfo.defaultDtype);
      }
    }
    inst.op = specializeOpcode(inst.op, inst.form);
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
  vfInfo.defaultDtype = normalizeDtype(vfInfo.defaultDtype);
  for (auto &[valueId, value] : vfInfo.values) {
    if (value.valueId.empty())
      value.valueId = valueId;
    if (value.valueId != valueId)
      throw std::runtime_error("VfInfo value map key does not match valueId: " +
                               valueId);
    value.dtype = normalizeDtype(value.dtype);
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
      if (node.kind == ProgramNode::Kind::Membar)
        continue;
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
