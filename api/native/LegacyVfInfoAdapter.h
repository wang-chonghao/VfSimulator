// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_LEGACY_VF_INFO_ADAPTER_H
#define VFSIM_API_NATIVE_LEGACY_VF_INFO_ADAPTER_H

#include "api/native/CanonicalVfInfo.h"
#include "api/native/VfInfo.h"

namespace vfsim {

// Offline migration helper. This symbol belongs to vfsim::native_legacy and is
// not part of the simulator runner API.
CanonicalVfInfo adaptLegacyVfInfoToCanonical(const VfInfo &vfInfo);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_LEGACY_VF_INFO_ADAPTER_H
