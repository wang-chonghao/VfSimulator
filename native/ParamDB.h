// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_PARAM_DB_H
#define VFSIM_NATIVE_PARAM_DB_H

#include "native/ParamSchema.h"

#include <filesystem>
#include <string>

namespace vfsim {

class ParamDB {
public:
  explicit ParamDB(std::filesystem::path baseDir = {});

  const ParamBundle &bundle() const noexcept { return bundle_; }
  const UarchConfig &uarch() const noexcept { return bundle_.uarch; }
  const IsaDefaults &isaDefaults() const noexcept { return bundle_.isaDefaults; }

  bool hasInst(const std::string &op, const std::string &dtype) const;
  const InstConfig &inst(const std::string &op, const std::string &dtype) const;

  int64_t forwardingCycles(const std::string &dtype, const std::string &prod,
                           const std::string &cons) const;
  int64_t forwardingCycles(const std::string &prod, const std::string &prodForm,
                           const std::string &cons,
                           const std::string &consForm) const;
  int64_t initiationInterval(const std::string &dtype, const std::string &prev,
                             const std::string &cur) const;
  int64_t initiationInterval(const std::string &prev,
                             const std::string &prevForm,
                             const std::string &cur,
                             const std::string &curForm) const;

  static std::filesystem::path resolveBaseDir(std::filesystem::path baseDir);

private:
  ParamBundle bundle_;
  std::filesystem::path baseDir_;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PARAM_DB_H
