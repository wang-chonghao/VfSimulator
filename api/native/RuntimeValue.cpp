// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/RuntimeValue.h"

#include <algorithm>
#include <cctype>

namespace vfsim {

ValueStorageKind inferValueStorage(const std::string &valueId) {
  std::string normalized = valueId;
  std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                 [](unsigned char c) {
                   return static_cast<char>(std::tolower(c));
                 });
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

} // namespace vfsim
