// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H
#define VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H

#include "api/native/CanonicalVfInfo.h"
#include "native/Json.h"

namespace vfsim {

// Test-only decoder used to enforce Python/C++ conformance on shared fixtures.
CanonicalVfInfo decodeCanonicalVfInfoFixture(const json::Value &root);

} // namespace vfsim

#endif // VFSIM_NATIVE_CANONICAL_VF_INFO_FIXTURE_DECODER_H
