// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_VALUE_STORAGE_H
#define VFSIM_NATIVE_VALUE_STORAGE_H

#include "api/native/VfInfo.h"

#include <string>
#include <unordered_map>

namespace vfsim {

class ValueStorageLookup {
public:
  ValueStorageLookup() = default;
  explicit ValueStorageLookup(const std::unordered_map<std::string, ValueInfo> &values) {
    for (const auto &[key, value] : values) {
      const std::string id = value.valueId.empty() ? key : value.valueId;
      storageById_.emplace(id, value.storage);
    }
  }

  ValueStorageKind storageOf(const std::string &name) const {
    auto it = storageById_.find(name);
    if (it != storageById_.end())
      return it->second;

    const std::string suffix = "_lane";
    const auto pos = name.rfind(suffix);
    if (pos != std::string::npos && pos + suffix.size() < name.size()) {
      bool digits = true;
      for (size_t i = pos + suffix.size(); i < name.size(); ++i) {
        if (name[i] < '0' || name[i] > '9') {
          digits = false;
          break;
        }
      }
      if (digits) {
        it = storageById_.find(name.substr(0, pos));
        if (it != storageById_.end())
          return it->second;
      }
    }

    return inferValueStorage(name);
  }

  bool isRegister(const std::string &name) const {
    return storageOf(name) == ValueStorageKind::Register;
  }

  bool isUB(const std::string &name) const {
    return storageOf(name) == ValueStorageKind::UB;
  }

private:
  std::unordered_map<std::string, ValueStorageKind> storageById_;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_VALUE_STORAGE_H
