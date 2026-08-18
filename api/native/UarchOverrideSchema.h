// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_UARCH_OVERRIDE_SCHEMA_H
#define VFSIM_API_NATIVE_UARCH_OVERRIDE_SCHEMA_H

#include <optional>
#include <string>

namespace vfsim {

enum class UarchOverrideFieldType { Integer, Boolean, String };

std::optional<UarchOverrideFieldType>
uarchOverrideFieldType(const std::string &name);
bool isDeprecatedUarchOverrideField(const std::string &name);
const char *uarchOverrideFieldTypeName(UarchOverrideFieldType type);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_UARCH_OVERRIDE_SCHEMA_H
