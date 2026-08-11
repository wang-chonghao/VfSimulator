// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_CONTROL_UNIT_H
#define VFSIM_NATIVE_CONTROL_UNIT_H

#include "native/IFU.h"
#include "native/ParamDB.h"

#include <functional>
#include <string>
#include <vector>

namespace vfsim {

class ControlUnit {
public:
  explicit ControlUnit(const ParamDB *db = nullptr);

  void acceptMembar(const DynamicInst &inst);
  void update(const std::function<bool(int64_t, const std::string &)> &hasPendingBefore);
  bool blocks(const DynamicInst &inst, const ParamDB &db,
              const std::string &dtype) const;
  bool empty() const noexcept { return barriers_.empty(); }

private:
  struct Barrier {
    int64_t streamSeq = -1;
    int64_t pc = -1;
    std::string barrier;
    std::string waitClass;
    std::string blockClass;
    bool released = false;
  };

  const ParamDB *db_ = nullptr;
  std::vector<Barrier> barriers_;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_CONTROL_UNIT_H
