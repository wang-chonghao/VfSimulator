// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/ControlUnit.h"

#include "native/ISATraits.h"

#include <algorithm>
#include <cctype>

namespace vfsim {
namespace {

std::string upper(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return value;
}

std::string normalizeBarrier(std::string value) {
  value = upper(std::move(value));
  const auto dot = value.rfind('.');
  if (dot != std::string::npos)
    value = value.substr(dot + 1);
  return value;
}

} // namespace

ControlUnit::ControlUnit(const ParamDB *db) : db_(db) {}

void ControlUnit::acceptMembar(const DynamicInst &inst) {
  const std::string barrier = normalizeBarrier(inst.barrier.empty() ? "VST_VLD" : inst.barrier);
  Barrier item;
  item.streamSeq = inst.streamSeq;
  item.pc = inst.pc;
  item.barrier = barrier;
  if (barrier == "VST_VLD") {
    item.waitClass = "STORE";
    item.blockClass = "LOAD";
  } else if (barrier == "VLD_VST") {
    item.waitClass = "LOAD";
    item.blockClass = "STORE";
  } else {
    if (db_ != nullptr) {
      db_->recordWarning(
          "unsupported_membar_type",
          {{"pc", std::to_string(inst.pc)},
           {"barrier", barrier.empty() ? inst.barrier : barrier},
           {"stream_seq", std::to_string(inst.streamSeq)}});
    }
    return;
  }
  barriers_.push_back(std::move(item));
}

void ControlUnit::update(
    const std::function<bool(int64_t, const std::string &)> &hasPendingBefore) {
  for (auto &barrier : barriers_) {
    if (barrier.released)
      continue;
    if (!hasPendingBefore(barrier.streamSeq, barrier.waitClass))
      barrier.released = true;
  }
  barriers_.erase(
      std::remove_if(barriers_.begin(), barriers_.end(),
                     [](const Barrier &barrier) { return barrier.released; }),
      barriers_.end());
}

bool ControlUnit::blocks(const DynamicInst &inst, const ParamDB &db,
                         const std::string &dtype) const {
  if (inst.type != "inst")
    return false;
  std::string cls;
  if (isLoadOp(db, inst.op, inst.form.empty() ? dtype : inst.form))
    cls = "LOAD";
  else if (isStoreOp(db, inst.op, inst.form.empty() ? dtype : inst.form))
    cls = "STORE";
  else
    return false;

  for (const auto &barrier : barriers_) {
    if (barrier.released)
      continue;
    if (inst.streamSeq > barrier.streamSeq && cls == barrier.blockClass)
      return true;
  }
  return false;
}

} // namespace vfsim
