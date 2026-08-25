// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H
#define VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H

#include "api/native/CanonicalVfInfo.h"
#include "native/Json.h"

namespace vfsim {

// Language-neutral CanonicalVfInfo v1 object decoder shared by the product
// JSON adapter and Python/C++ conformance tests.
CanonicalVfInfo decodeCanonicalVfInfoJson(const json::Value &root);

} // namespace vfsim

#endif // VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H
