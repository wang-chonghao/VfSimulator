// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_JSON_VF_INFO_ADAPTER_H
#define VFSIM_API_NATIVE_JSON_VF_INFO_ADAPTER_H

#include "api/native/VfInfo.h"

#include <filesystem>

namespace vfsim {

VfInfo loadJsonVfInfo(const std::filesystem::path &path);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_JSON_VF_INFO_ADAPTER_H
