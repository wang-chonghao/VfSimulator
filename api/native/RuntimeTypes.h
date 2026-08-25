// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_RUNTIME_TYPES_H
#define VFSIM_API_NATIVE_RUNTIME_TYPES_H

#include <cstdint>
#include <string>
#include <unordered_map>

namespace vfsim {

using RuntimeParamMap = std::unordered_map<std::string, int64_t>;

} // namespace vfsim

#endif // VFSIM_API_NATIVE_RUNTIME_TYPES_H
