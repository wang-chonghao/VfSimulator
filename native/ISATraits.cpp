// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ISATraits.h"

#include <cctype>
#include <algorithm>

namespace vfsim {
namespace {

std::string canonicalOp(std::string op) {
  std::transform(op.begin(), op.end(), op.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return op;
}

OpClass opClassFromString(const std::string &text) {
  const std::string canon = canonicalOp(text);
  if (canon == "LOAD")
    return OpClass::Load;
  if (canon == "STORE")
    return OpClass::Store;
  if (canon == "COMPUTE")
    return OpClass::Compute;
  return OpClass::Unknown;
}

OpClass opClassFromNameFallback(const std::string &op) {
  const std::string canon = canonicalOp(op);
  if (canon == "VLDS" || canon == "VLD")
    return OpClass::Load;
  if (canon == "VSTS" || canon == "VST" || canon == "VSTUS" ||
      canon == "VSTAS")
    return OpClass::Store;
  return OpClass::Compute;
}

} // namespace

OpClass getOpClass(const ParamDB &db, const std::string &op,
                   const std::string &dtype) {
  const std::string canonOp = canonicalOp(op);
  if (db.hasInst(canonOp, dtype)) {
    const InstConfig &cfg = db.inst(canonOp, dtype);
    const OpClass opClass = opClassFromString(cfg.opClass);
    if (opClass != OpClass::Unknown)
      return opClass;
    if (!cfg.exu.empty() || !cfg.dispatchExu.empty())
      return OpClass::Compute;
  }
  return opClassFromNameFallback(canonOp);
}

bool isLoadOp(const ParamDB &db, const std::string &op, const std::string &dtype) {
  return getOpClass(db, op, dtype) == OpClass::Load;
}

bool isStoreOp(const ParamDB &db, const std::string &op, const std::string &dtype) {
  return getOpClass(db, op, dtype) == OpClass::Store;
}

bool isComputeOp(const ParamDB &db, const std::string &op, const std::string &dtype) {
  return getOpClass(db, op, dtype) == OpClass::Compute;
}

bool usesLsq(const ParamDB &db, const std::string &op, const std::string &dtype) {
  const OpClass cls = getOpClass(db, op, dtype);
  return cls == OpClass::Load || cls == OpClass::Store;
}

bool usesShqQueue(const ParamDB &db, const std::string &op, const std::string &dtype) {
  return isComputeOp(db, op, dtype);
}

bool usesSharedShqCredit(const ParamDB &db, const std::string &op, const std::string &dtype) {
  const OpClass cls = getOpClass(db, op, dtype);
  return cls == OpClass::Compute || cls == OpClass::Store;
}

} // namespace vfsim
