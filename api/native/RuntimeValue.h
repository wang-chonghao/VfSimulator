// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_RUNTIME_VALUE_H
#define VFSIM_API_NATIVE_RUNTIME_VALUE_H

#include <cstdint>
#include <string>
#include <vector>

namespace vfsim {

enum class ValueStorageKind { Register, UB, Scalar };

struct ValueInfo {
  std::string valueId;
  ValueStorageKind storage = ValueStorageKind::Register;
  std::string dtype;
  std::vector<int64_t> shape;
};

ValueStorageKind inferValueStorage(const std::string &valueId);
std::string valueStorageName(ValueStorageKind storage);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_RUNTIME_VALUE_H
