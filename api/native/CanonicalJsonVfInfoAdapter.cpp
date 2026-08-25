// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/CanonicalJsonVfInfoAdapter.h"

#include "native/CanonicalVfInfoFixtureDecoder.h"
#include "native/Json.h"

#include <sstream>
#include <stdexcept>

namespace vfsim {

CanonicalVfInfo loadCanonicalJsonVfInfo(const std::filesystem::path &path) {
  CanonicalVfInfo vfInfo = decodeCanonicalVfInfoJson(json::parseFile(path));
  const CanonicalValidationResult validation = validateCanonicalVfInfo(vfInfo);
  if (validation.ok())
    return vfInfo;

  std::ostringstream message;
  message << "CanonicalVfInfo validation failed";
  for (const auto &diagnostic : validation.diagnostics) {
    if (diagnostic.severity != "error")
      continue;
    message << "; " << diagnostic.code << " at " << diagnostic.path << ": "
            << diagnostic.message;
  }
  throw std::runtime_error(message.str());
}

} // namespace vfsim
