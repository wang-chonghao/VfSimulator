// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_VF_INFO_H
#define VFSIM_API_NATIVE_VF_INFO_H

#include "api/native/RuntimeValue.h"

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct ProgramInstNode {
  std::string op;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::string form;
};

struct ProgramMembarNode {
  std::string barrier;
};

struct ProgramLoopNode;

struct ProgramNode {
  enum class Kind { Inst, Loop, Membar };

  Kind kind = Kind::Inst;
  ProgramInstNode inst;
  ProgramMembarNode membar;
  std::shared_ptr<ProgramLoopNode> loop;

  static ProgramNode makeInst(ProgramInstNode value);
  static ProgramNode makeLoop(ProgramLoopNode value);
  static ProgramNode makeMembar(ProgramMembarNode value);
};

struct ProgramLoopNode {
  std::string iters;
  std::string unroll = "1";
  std::string name;
  std::vector<ProgramNode> body;
};

struct VfInfo {
  std::unordered_map<std::string, ValueInfo> values;
  std::vector<ProgramNode> body;
  std::unordered_map<std::string, int64_t> params;
  std::string defaultDtype = "fp32";
};

void canonicalizeVfInfo(VfInfo &vfInfo);
void lowerVfInfoValueIds(VfInfo &vfInfo);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_VF_INFO_H
