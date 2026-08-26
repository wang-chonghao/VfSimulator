// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/OOO.h"

#include "native/ControlUnit.h"
#include "native/ISATraits.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

std::string jsonEscape(const std::string &text) {
  std::string out;
  out.reserve(text.size() + 8);
  for (char c : text) {
    switch (c) {
    case '\\':
      out += "\\\\";
      break;
    case '"':
      out += "\\\"";
      break;
    case '\n':
      out += "\\n";
      break;
    case '\r':
      out += "\\r";
      break;
    case '\t':
      out += "\\t";
      break;
    default:
      out.push_back(c);
      break;
    }
  }
  return out;
}

template <typename T>
std::string joinJsonArray(const std::vector<T> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    oss << values[i];
  }
  oss << "]";
  return oss.str();
}

template <>
std::string joinJsonArray<std::string>(const std::vector<std::string> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    oss << '"' << jsonEscape(values[i]) << '"';
  }
  oss << "]";
  return oss.str();
}

template <>
std::string joinJsonArray<std::optional<std::string>>(const std::vector<std::optional<std::string>> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    if (values[i].has_value())
      oss << '"' << jsonEscape(*values[i]) << '"';
    else
      oss << "null";
  }
  oss << "]";
  return oss.str();
}

std::string joinIterationPath(
    const std::vector<std::pair<std::string, int64_t>> &path) {
  std::ostringstream oss;
  oss << "[";
  for (size_t index = 0; index < path.size(); ++index) {
    if (index)
      oss << ", ";
    oss << "{\"loop_id\":\"" << jsonEscape(path[index].first)
        << "\",\"iteration\":" << path[index].second << "}";
  }
  oss << "]";
  return oss.str();
}

} // namespace

OoOCore::OoOCore(const UarchConfig &uarch, const ParamDB &db, std::string dtype,
                 const std::unordered_map<std::string, ValueInfo> &values)
    : db_(db), dtype_(std::move(dtype)), valueStorage_(values) {
  theoreticalLimitMode_ = false;
  enableIsuQueueModel_ = uarch.enableIsuQueueModel;
  loadPorts_ = static_cast<int>(uarch.loadPorts);
  issuePorts_ = static_cast<int>(uarch.issuePorts);
  threePortsMode_ = uarch.threePortsMode;
  storePorts_ = static_cast<int>(uarch.storePorts);
  ubSlots_ = static_cast<int>(uarch.ubSlots);
  lsuStorePriorityPregThreshold_ =
      static_cast<int>(uarch.lsuStorePriorityPregThreshold);
  if (loadPorts_ <= 0 || storePorts_ <= 0 || ubSlots_ <= 0)
    throw std::invalid_argument(
        "load_ports, store_ports, and ub_slots must be positive");
  if (lsuStorePriorityPregThreshold_ < 0)
    throw std::invalid_argument(
        "lsu_store_priority_preg_threshold must be non-negative");
  shqDepth_ = static_cast<int>(uarch.shqDepth);
  lsqDepth_ = static_cast<int>(uarch.ldqWidth ? uarch.ldqWidth : 24);
  pregNum_ = static_cast<int>(uarch.vregNum ? uarch.vregNum : 68);
  vfStartupCost_ = static_cast<int>(db_.isaDefaults().vfStartupCost);
  vfDrainCost_ = static_cast<int>(db_.isaDefaults().vfDrainCost);
  freelist_.clear();
  for (int i = 0; i < pregNum_; ++i)
    freelist_.push_back("p" + std::to_string(i));
  visiblePregFree_ = pregNum_;
  lastIssueCycleALU_.assign(issuePorts_, -1000000000);
  lastIssueCycleSFU_.assign(issuePorts_, -1000000000);
  lastOpALU_.assign(issuePorts_, "");
  lastFormALU_.assign(issuePorts_, "");
  lastOpSFU_.assign(issuePorts_, "");
  lastFormSFU_.assign(issuePorts_, "");
  lastIssueCycleExu_.assign(issuePorts_, -1000000000);
  lastOpExu_.assign(issuePorts_, "");
  lastFormExu_.assign(issuePorts_, "");
  exqInflight_.assign(issuePorts_, 0);
  oooToShqDelay_ = static_cast<int>(uarch.oooToShqDelay ? uarch.oooToShqDelay : 1);
  oooToLsqDelay_ = static_cast<int>(uarch.oooToLsqDelay ? uarch.oooToLsqDelay : 1);
  exqRecvDelay_ = static_cast<int>(uarch.exqRecvDelay ? uarch.exqRecvDelay : 1);
  enforceSameCycleSrcHazard_ = uarch.enforceSameCycleSrcHazard;
  enableExqGreedyBalance_ = false;
  enableShqCreditModel_ = uarch.enableShqCreditModel;
  enableCreditVisibilityDelay_ = uarch.enableCreditVisibilityDelay;
  enableCrossFuIi_ = uarch.enableCrossFuIi;
  exqCapacityCountsInflight_ = uarch.exqCapacityCountsInflight;
  exqDepth_ = static_cast<int>(uarch.exqDepth ? uarch.exqDepth : 26);
  shqToExqPortPerCycle_ = static_cast<int>(uarch.shqToExqPortPerCycle ? uarch.shqToExqPortPerCycle : 1);
  shqExqDispatchPolicy_ = uarch.shqExqDispatchPolicy;
  std::transform(shqExqDispatchPolicy_.begin(), shqExqDispatchPolicy_.end(),
                 shqExqDispatchPolicy_.begin(), [](unsigned char c) {
                   return static_cast<char>(std::tolower(c));
                 });
  exu0ReserveLookahead_ = static_cast<int>(uarch.exu0ReserveLookahead);
  exu0ReserveMinCount_ = std::max<int>(1, static_cast<int>(uarch.exu0ReserveMinCount));
  exqIssueInflightCapPerPort_ = static_cast<int>(uarch.exqIssueInflightCapPerPort);
  computeInflightCap_ = static_cast<int>(uarch.computeInflightCap);
  shqReleaseDelay_ = static_cast<int>(uarch.shqReleaseDelay ? uarch.shqReleaseDelay : 1);
  iduVisiblePregDelay_ = static_cast<int>(uarch.iduVisiblePregDelay);
  iduVisibleShqDelay_ = static_cast<int>(uarch.iduVisibleShqDelay);
  visibleShqUsed_ = 0;
}

int OoOCore::getFreePreg() const {
  if (theoreticalLimitMode_)
    return 1000000000;
  if (enableCreditVisibilityDelay_)
    return std::max(0, visiblePregFree_);
  return static_cast<int>(freelist_.size());
}

int OoOCore::getFreeShqQueue() const {
  return theoreticalLimitMode_ ? 1000000000 : std::max(0, shqDepth_ - static_cast<int>(shq_.size()));
}

int OoOCore::getFreeLsq() const {
  return theoreticalLimitMode_ ? 1000000000 : std::max(0, lsqDepth_ - static_cast<int>(lsq_.size()));
}

int OoOCore::getFreeShq() const {
  if (theoreticalLimitMode_ || !enableShqCreditModel_)
    return 1000000000;
  if (enableCreditVisibilityDelay_)
    return std::max(0, shqDepth_ - visibleShqUsed_);
  return std::max(0, shqDepth_ - shqUsed_);
}

std::optional<int> OoOCore::getExuPortForInst(int64_t instId) const {
  const Uop *uop = findRobUop(instId);
  if (uop == nullptr || uop->exuPort < 0)
    return std::nullopt;
  return uop->exuPort;
}

std::optional<std::string> OoOCore::getPregSrcForInst(int64_t instId,
                                                      size_t index) const {
  const Uop *uop = findRobUop(instId);
  if (uop == nullptr || index >= uop->pregSrc.size())
    return std::nullopt;
  return uop->pregSrc[index];
}

std::optional<std::string> OoOCore::getPregDstForInst(int64_t instId,
                                                      size_t index) const {
  const Uop *uop = findRobUop(instId);
  if (uop == nullptr || index >= uop->pregDst.size() || uop->pregDst[index].empty())
    return std::nullopt;
  return uop->pregDst[index];
}

bool OoOCore::hasPendingLsuBefore(int64_t streamSeq,
                                  const std::string &opClass) const {
  const std::string target = [&]() {
    std::string value = opClass;
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
      return static_cast<char>(std::toupper(c));
    });
    return value;
  }();
  for (const auto &u : rob_) {
    if (u.streamSeq < 0 || u.streamSeq >= streamSeq)
      continue;
    if (u.state == "done")
      continue;
    std::string cls;
    if (u.opClass == "LOAD")
      cls = "LOAD";
    else if (u.opClass == "STORE")
      cls = "STORE";
    if (cls == target)
      return true;
  }
  return false;
}

std::unordered_map<std::string, int> OoOCore::updateIduVisibility(int64_t cycle) {
  if (!enableCreditVisibilityDelay_)
    return std::unordered_map<std::string, int>{{"preg_free", 0}, {"shq_release", 0}};
  int pregDelta = iduMailboxPregReleaseDelta_;
  int shqDelta = iduMailboxShqReleaseDelta_;
  iduMailboxPregReleaseDelta_ = 0;
  iduMailboxShqReleaseDelta_ = 0;

  auto pit = visiblePregFreeEvents_.find(cycle);
  if (pit != visiblePregFreeEvents_.end()) {
    visiblePregFree_ += pit->second;
    pregDelta += pit->second;
    visiblePregFreeEvents_.erase(pit);
  }
  auto sit = visibleShqReleaseEvents_.find(cycle);
  if (sit != visibleShqReleaseEvents_.end()) {
    visibleShqUsed_ = std::max(0, visibleShqUsed_ - sit->second);
    shqDelta += sit->second;
    visibleShqReleaseEvents_.erase(sit);
  }
  return std::unordered_map<std::string, int>{{"preg_free", pregDelta}, {"shq_release", shqDelta}};
}

int64_t OoOCore::vfEndCycle() const {
  return lastDoneCycle_ + vfDrainCost_;
}

std::string OoOCore::classifyOpClass(const std::string &op,
                                     const std::string &form) const {
  return isLoadOp(db_, op, form)
             ? "LOAD"
             : (isStoreOp(db_, op, form) ? "STORE" : "COMPUTE");
}

bool OoOCore::isRegisterValue(const std::string &name) const {
  return valueStorage_.isRegister(name);
}

int64_t OoOCore::computeReadyTimeForSrc(
    const ProducerInfo &producerInfo, const std::string &consumerOp,
    const std::string &consumerForm) const {
  const std::string cacheKey = producerInfo.op + "." + producerInfo.form +
                               "\x1f" + consumerOp + "." + consumerForm;
  auto cached = forwardingPairCache_.find(cacheKey);
  int64_t fwd = 0;
  if (cached != forwardingPairCache_.end()) {
    fwd = cached->second;
  } else {
    fwd = db_.forwardingCycles(producerInfo.op, producerInfo.form, consumerOp,
                               consumerForm);
    forwardingPairCache_[cacheKey] = fwd;
  }
  if (isComputeOp(db_, consumerOp, consumerForm) && enableIsuQueueModel_)
    return producerInfo.startCycle + std::max<int64_t>(0, fwd - 1);
  return producerInfo.startCycle + fwd;
}

int64_t OoOCore::computeLoadReadyCycle(const Uop &u) const {
  return std::max<int64_t>(vfStartupCost_, u.lsqReadyCycle);
}

bool OoOCore::blockedByControlUnit(const Uop &u) const {
  if (controlUnit_ == nullptr)
    return false;
  DynamicInst inst;
  inst.type = "inst";
  inst.op = u.op;
  inst.form = u.form;
  inst.streamSeq = u.streamSeq;
  return controlUnit_->blocks(inst, db_, dtype_);
}

std::tuple<int64_t, std::optional<std::string>,
           std::optional<std::string>, std::optional<int64_t>>
OoOCore::computeStoreReadyCycle(const Uop &u) const {
  for (const auto &ps : u.pregSrc) {
    if (!ps.has_value())
      continue;
    if (pregPending_.count(*ps) && pregProducer_.find(*ps) == pregProducer_.end())
      return {1000000000, std::nullopt, std::nullopt, std::nullopt};
  }

  int64_t bestT = -1;
  std::optional<std::string> pop;
  std::optional<std::string> pform;
  std::optional<int64_t> pst;
  for (const auto &ps : u.pregSrc) {
    if (!ps.has_value())
      continue;
    auto it = pregProducer_.find(*ps);
    if (it == pregProducer_.end())
      continue;
    const auto &kind = it->second.kind;
    if (kind != "COMPUTE" && kind != "LOAD")
      continue;
    const int64_t cand = computeReadyTimeForSrc(it->second, u.op, u.form);
    if (cand > bestT) {
      bestT = cand;
      pop = it->second.op;
      pform = it->second.form;
      pst = it->second.startCycle;
    }
  }
  bool hasDependency = bestT >= 0;
  if (u.alignStateOperation == "consume" && u.alignGeneration) {
    int64_t stateReady = u.lsqReadyCycle;
    for (const auto &record : u.alignGeneration->producers) {
      if (!record || !record->startCycle.has_value())
        return {1000000000, pop, pform, pst};
      const ProducerInfo producer{record->op, record->form,
                                  *record->startCycle, "ALIGN_STATE"};
      stateReady = std::max(
          stateReady, computeReadyTimeForSrc(producer, u.op, u.form));
    }
    bestT = std::max(bestT, stateReady);
    hasDependency = true;
  }
  if (!hasDependency)
    return {1000000000, std::nullopt, std::nullopt, std::nullopt};
  bestT = std::max<int64_t>(bestT, u.lsqReadyCycle);
  return {bestT, pop, pform, pst};
}

void OoOCore::bindAlignState(Uop &u, const DynamicInst &inst) {
  if ((inst.alignStateOperation != "append" &&
       inst.alignStateOperation != "consume") ||
      inst.alignStateId.empty())
    return;

  auto found = alignStateOpen_.find(inst.alignStateId);
  std::shared_ptr<AlignGeneration> generation;
  if (found == alignStateOpen_.end()) {
    const int64_t generationId = alignStateNextGeneration_[inst.alignStateId]++;
    generation = std::make_shared<AlignGeneration>();
    generation->stateId = inst.alignStateId;
    generation->generationId = generationId;
    alignStateOpen_[inst.alignStateId] = generation;
  } else {
    generation = found->second;
  }

  u.alignStateOperation = inst.alignStateOperation;
  u.alignStateId = inst.alignStateId;
  u.alignGeneration = generation;
  if (inst.alignStateOperation == "append") {
    auto record = std::make_shared<AlignProducerRecord>();
    record->instId = u.instId;
    record->streamSeq = u.streamSeq;
    record->op = u.op;
    record->form = u.form;
    generation->producers.push_back(record);
    u.alignProducerRecord = std::move(record);
    return;
  }

  generation->consumerInstId = u.instId;
  auto next = std::make_shared<AlignGeneration>();
  next->stateId = inst.alignStateId;
  next->generationId = alignStateNextGeneration_[inst.alignStateId]++;
  alignStateOpen_[inst.alignStateId] = std::move(next);
}

std::string OoOCore::getFuType(const std::string &op,
                               const std::string &form) const {
  const auto &cfg = db_.inst(op, form);
  std::string fu = cfg.exu.empty() ? "ALU" : cfg.exu;
  std::transform(fu.begin(), fu.end(), fu.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  if (fu != "ALU" && fu != "SFU")
    fu = "ALU";
  return fu;
}

std::vector<int> OoOCore::eligibleExuPorts(const std::string &op,
                                           const std::string &form) const {
  std::string tag = db_.inst(op, form).dispatchExu;
  std::transform(tag.begin(), tag.end(), tag.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  if (tag == "EXU0_ONLY")
    return issuePorts_ > 0 ? std::vector<int>{0} : std::vector<int>{};
  if (tag == "EXU01") {
    if (threePortsMode_) {
      std::vector<int> out;
      for (int port = 0; port < std::min(issuePorts_, 3); ++port)
        out.push_back(port);
      return out;
    } else {
      return issuePorts_ >= 2 ? std::vector<int>{0, 1} : std::vector<int>{0};
    }
  }
  if (tag == "EXU012")
    return issuePorts_ >= 3 ? std::vector<int>{0, 1, 2} : std::vector<int>{0, 1};
  std::vector<int> out;
  for (int i = 0; i < issuePorts_; ++i)
    out.push_back(i);
  return out;
}

std::vector<int> OoOCore::eligibleExuPorts(const Uop &u) const {
  if (u.dispatchExu == "EXU0_ONLY")
    return issuePorts_ > 0 ? std::vector<int>{0} : std::vector<int>{};
  if (u.dispatchExu == "EXU01") {
    std::vector<int> out;
    const int limit = threePortsMode_ ? std::min(issuePorts_, 3)
                                      : std::min(issuePorts_, 2);
    for (int port = 0; port < limit; ++port)
      out.push_back(port);
    return out;
  }
  if (u.dispatchExu == "EXU012") {
    std::vector<int> out;
    for (int port = 0; port < std::min(issuePorts_, 3); ++port)
      out.push_back(port);
    return out;
  }
  std::vector<int> out;
  for (int port = 0; port < issuePorts_; ++port)
    out.push_back(port);
  return out;
}

std::vector<int> OoOCore::getEligibleExuPorts(const std::string &op,
                                              const std::string &form) const {
  return eligibleExuPorts(op, form);
}

int64_t OoOCore::getIi(const std::string *prevOp,
                       const std::string *prevForm,
                       const std::string &curOp,
                       const std::string &curForm) const {
  if (!prevOp || !prevForm || prevOp->empty())
    return 1;
  const std::string cacheKey = *prevOp + "." + *prevForm + "\x1f" + curOp +
                               "." + curForm;
  const auto cached = initiationIntervalPairCache_.find(cacheKey);
  if (cached != initiationIntervalPairCache_.end())
    return cached->second;
  const int64_t value =
      db_.initiationInterval(*prevOp, *prevForm, curOp, curForm);
  initiationIntervalPairCache_[cacheKey] = value;
  return value;
}

void OoOCore::log(const std::string &event, const Uop &u) {
  history_.push_back(HistoryRecord{
      cycle_, event, u.instId, u.op, u.state, u.blockedReason, u.readyCycle, u.startCycle,
      u.doneCycle, u.src, u.dst, u.pregSrc, u.pregDst, u.pregOld,
      u.producerOpForStore, u.producerStartForStore, u.staticInstructionId,
      u.iterationPath, u.streamSeq});
}

void OoOCore::logMembarBlocked(Uop &u) {
  const auto oldReason = u.blockedReason;
  u.blockedReason = "membar";
  log("blocked", u);
  u.blockedReason = oldReason;
}

void OoOCore::logStartSimple(const Uop &u) {
  startLogs_.push_back(SimpleLogRecord{cycle_, u.instId, u.op, u.dst, u.src,
                                       u.staticInstructionId, u.iterationPath,
                                       u.streamSeq});
}

void OoOCore::logDoneSimple(const Uop &u) {
  doneLogs_.push_back(SimpleLogRecord{u.doneCycle.value_or(cycle_), u.instId,
                                      u.op, u.dst, u.src,
                                      u.staticInstructionId, u.iterationPath,
                                      u.streamSeq});
}

void OoOCore::dumpHistory(const std::string &path) const {
  std::ofstream os(path);
  os << "[\n";
  for (size_t i = 0; i < history_.size(); ++i) {
    const auto &h = history_[i];
    os << "  {"
       << "\"cy\":" << h.cy << ","
       << "\"event\":\"" << jsonEscape(h.event) << "\","
       << "\"id\":" << h.id << ","
       << "\"op\":\"" << jsonEscape(h.op) << "\","
       << "\"state\":\"" << jsonEscape(h.state) << "\","
       << "\"blocked_reason\":" << (h.blockedReason ? "\"" + jsonEscape(*h.blockedReason) + "\"" : "null") << ","
       << "\"ready\":" << h.ready << ","
       << "\"start\":" << (h.start ? std::to_string(*h.start) : "null") << ","
       << "\"done\":" << (h.done ? std::to_string(*h.done) : "null") << ","
       << "\"src\":" << joinJsonArray(h.src) << ","
       << "\"dst\":" << joinJsonArray(h.dst) << ","
       << "\"preg_src\":" << joinJsonArray(h.pregSrc) << ","
       << "\"preg_dst\":" << joinJsonArray(h.pregDst) << ","
       << "\"preg_old\":" << joinJsonArray(h.pregOld) << ","
       << "\"producer_op_for_store\":" << (h.producerOpForStore ? "\"" + jsonEscape(*h.producerOpForStore) + "\"" : "null") << ","
       << "\"producer_start_for_store\":" << (h.producerStartForStore ? std::to_string(*h.producerStartForStore) : "null") << ","
       << "\"static_instruction_id\":\"" << jsonEscape(h.staticInstructionId) << "\","
       << "\"iteration_path\":" << joinIterationPath(h.iterationPath) << ","
       << "\"stream_seq\":" << h.streamSeq
       << "}";
    if (i + 1 < history_.size())
      os << ",";
    os << "\n";
  }
  os << "]\n";
}

void OoOCore::dumpSimpleLogs(const std::string &startPath, const std::string &donePath) const {
  std::ofstream s(startPath);
  for (const auto &r : startLogs_) {
    s << "{\"cy\":" << r.cy << ",\"inst_id\":" << r.instId << ",\"op\":\"" << jsonEscape(r.op)
      << "\",\"dst\":" << joinJsonArray(r.dst) << ",\"src\":" << joinJsonArray(r.src)
      << ",\"static_instruction_id\":\"" << jsonEscape(r.staticInstructionId) << "\""
      << ",\"iteration_path\":" << joinIterationPath(r.iterationPath)
      << ",\"stream_seq\":" << r.streamSeq << "}\n";
  }
  std::ofstream d(donePath);
  for (const auto &r : doneLogs_) {
    d << "{\"cy\":" << r.cy << ",\"inst_id\":" << r.instId << ",\"op\":\"" << jsonEscape(r.op)
      << "\",\"dst\":" << joinJsonArray(r.dst) << ",\"src\":" << joinJsonArray(r.src)
      << ",\"static_instruction_id\":\"" << jsonEscape(r.staticInstructionId) << "\""
      << ",\"iteration_path\":" << joinIterationPath(r.iterationPath)
      << ",\"stream_seq\":" << r.streamSeq << "}\n";
  }
}

Uop *OoOCore::findRobUop(int64_t instId) {
  for (auto &u : rob_) {
    if (u.instId == instId)
      return &u;
  }
  return nullptr;
}

const Uop *OoOCore::findRobUop(int64_t instId) const {
  for (const auto &u : rob_) {
    if (u.instId == instId)
      return &u;
  }
  return nullptr;
}

bool OoOCore::isCurrentMapping(const std::string &preg) const {
  for (const auto &[_, cur] : rat_) {
    if (cur == preg)
      return true;
  }
  return false;
}

void OoOCore::scheduleSrcReleaseFromStart(const Uop &u) {
  if (!u.startCycle.has_value())
    return;
  if (srcReleaseScheduledInstIds_.count(u.instId))
    return;
  const int64_t releaseCycle = *u.startCycle + consumerReleaseStartOffset_;
  auto &bucket = srcReleaseEvents_[releaseCycle];
  for (size_t i = 0; i < u.pregSrc.size(); ++i) {
    const auto &s = u.pregSrc[i];
    if (!s.has_value())
      continue;
    const int64_t gen = (i < u.pregSrcGen.size() && u.pregSrcGen[i].has_value())
                            ? *u.pregSrcGen[i]
                            : pregGeneration_[*s];
    bucket.push_back(SrcReleaseEvent{u.instId, *s, gen});
    srcReleaseExpected_[u.instId] += 1;
  }
  srcReleaseSeen_[u.instId] = 0;
  srcReleaseScheduledInstIds_.insert(u.instId);
}

void OoOCore::runSrcReleaseEvents(int64_t cycle) {
  auto it = srcReleaseEvents_.find(cycle);
  if (it == srcReleaseEvents_.end())
    return;
  for (const auto &ev : it->second) {
    ++srcReleaseSeen_[ev.instId];
    const auto genIt = pregGeneration_.find(ev.preg);
    if (genIt == pregGeneration_.end() || genIt->second != ev.gen)
      continue;
    auto cntIt = pregConsumerCount_.find(ev.preg);
    if (cntIt != pregConsumerCount_.end() && cntIt->second > 0) {
      --cntIt->second;
      if (cntIt->second == 0)
        pregReleaseEligibleCycle_[ev.preg] = cycle;
    }
  }
  srcReleaseEvents_.erase(it);
}

bool OoOCore::tryFreePreg(const std::string &preg, int64_t cycle) {
  if (preg.empty() || isCurrentMapping(preg))
    return false;
  auto cntIt = pregConsumerCount_.find(preg);
  if (cntIt != pregConsumerCount_.end() && cntIt->second > 0)
    return false;
  auto eligIt = pregReleaseEligibleCycle_.find(preg);
  if (eligIt != pregReleaseEligibleCycle_.end() && cycle < eligIt->second)
    return false;
  if (std::find(freelist_.begin(), freelist_.end(), preg) != freelist_.end())
    return false;
  if (pregPending_.count(preg))
    return false;
  pregProducer_.erase(preg);
  pregPending_.erase(preg);
  pregConsumerCount_.erase(preg);
  pregReleaseEligibleCycle_.erase(preg);
  freelist_.push_back(preg);
  if (enableCreditVisibilityDelay_) {
    if (iduVisiblePregDelay_ <= 0) {
      ++visiblePregFree_;
      ++iduMailboxPregReleaseDelta_;
    } else {
      visiblePregFreeEvents_[cycle + iduVisiblePregDelay_] += 1;
    }
  }
  return true;
}

void OoOCore::tryFreeEligiblePregs(int64_t cycle) {
  std::vector<std::string> elig;
  elig.reserve(pregReleaseEligibleCycle_.size());
  for (const auto &[preg, _] : pregReleaseEligibleCycle_)
    elig.push_back(preg);
  for (const auto &preg : elig)
    (void)tryFreePreg(preg, cycle);
}

int OoOCore::exqOccupancy(int port) const {
  if (port < 0 || port >= static_cast<int>(exqInflight_.size()))
    return 0;
  return exqInflight_[static_cast<size_t>(port)] * (exqCapacityCountsInflight_ ? 1 : 0);
}

int OoOCore::totalComputeInflight() const {
  int total = 0;
  for (int x : exqInflight_)
    total += x;
  return total;
}

int64_t OoOCore::predictExqIssueCycle(int port, const std::string &fuType,
                                      const std::string &op,
                                      const std::string &form,
                                      int64_t recvCycle) const {
  int64_t pred = recvCycle;
  const std::string *prevOp = nullptr;
  const std::string *prevForm = nullptr;
  int64_t prevIssue = -1000000000;
  if (enableCrossFuIi_) {
    prevOp = &lastOpExu_[static_cast<size_t>(port)];
    prevForm = &lastFormExu_[static_cast<size_t>(port)];
    prevIssue = lastIssueCycleExu_[static_cast<size_t>(port)];
  } else if (fuType == "SFU") {
    prevOp = &lastOpSFU_[static_cast<size_t>(port)];
    prevForm = &lastFormSFU_[static_cast<size_t>(port)];
    prevIssue = lastIssueCycleSFU_[static_cast<size_t>(port)];
  } else {
    prevOp = &lastOpALU_[static_cast<size_t>(port)];
    prevForm = &lastFormALU_[static_cast<size_t>(port)];
    prevIssue = lastIssueCycleALU_[static_cast<size_t>(port)];
  }
  pred = std::max<int64_t>(pred, prevIssue + getIi(prevOp, prevForm, op, form));
  return pred;
}

bool OoOCore::useFuRoundRobinFifo() const {
  return shqExqDispatchPolicy_ == "fu_round_robin_fifo" ||
         shqExqDispatchPolicy_ == "fu_rr_fifo" ||
         shqExqDispatchPolicy_ == "fu_round_robin" ||
         shqExqDispatchPolicy_ == "fu_round_robin_exu0_reserve";
}

bool OoOCore::useExu0Reserve() const {
  return shqExqDispatchPolicy_ == "fu_round_robin_exu0_reserve";
}

int OoOCore::exu0OnlyPressureCount(size_t startIndex) const {
  if (exu0ReserveLookahead_ <= 0)
    return 0;
  int seenCompute = 0;
  int seenExu0Only = 0;
  for (size_t index = startIndex + 1; index < shq_.size(); ++index) {
    const Uop &candidate = shq_[index];
    ++seenCompute;
    if (candidate.dispatchExu == "EXU0_ONLY")
      ++seenExu0Only;
    if (seenCompute >= exu0ReserveLookahead_)
      break;
  }
  return seenExu0Only >= exu0ReserveMinCount_ ? seenExu0Only : 0;
}

int OoOCore::selectFuRoundRobinPort(const std::string &fuType,
                                    const std::vector<int> &candidates) {
  if (candidates.empty())
    throw std::invalid_argument("selectFuRoundRobinPort requires candidates");
  const int ptr = shqExqRrPtrByFu_[fuType];
  for (int offset = 0; offset < std::max(1, issuePorts_); ++offset) {
    const int port = (ptr + offset) % std::max(1, issuePorts_);
    if (std::find(candidates.begin(), candidates.end(), port) == candidates.end())
      continue;
    shqExqRrPtrByFu_[fuType] = (port + 1) % std::max(1, issuePorts_);
    return port;
  }
  const int chosen = *std::min_element(candidates.begin(), candidates.end());
  shqExqRrPtrByFu_[fuType] = (chosen + 1) % std::max(1, issuePorts_);
  return chosen;
}

void OoOCore::scheduleShqRelease(int64_t cycle, int count) {
  if (!enableShqCreditModel_ || count <= 0)
    return;
  shqReleaseEvents_[cycle + shqReleaseDelay_] += count;
}

void OoOCore::runShqReleaseEvents(int64_t cycle) {
  if (!enableShqCreditModel_)
    return;
  auto it = shqReleaseEvents_.find(cycle);
  if (it == shqReleaseEvents_.end())
    return;
  const int released = it->second;
  shqReleaseEvents_.erase(it);
  shqUsed_ = std::max(0, shqUsed_ - released);
  if (!enableCreditVisibilityDelay_)
    return;
  if (iduVisibleShqDelay_ <= 0) {
    visibleShqUsed_ = std::max(0, visibleShqUsed_ - released);
    iduMailboxShqReleaseDelta_ += released;
  } else {
    visibleShqReleaseEvents_[cycle + iduVisibleShqDelay_] += released;
  }
}

OoOCoreMainline::OoOCoreMainline(const UarchConfig &uarch, const ParamDB &db, std::string dtype,
                                 const std::unordered_map<std::string, ValueInfo> &values)
    : OoOCore(uarch, db, std::move(dtype), values) {
  exqInflightPerPort_.assign(issuePorts_, 0);
  exqWait_.resize(static_cast<size_t>(issuePorts_));
  for (auto &port : exqWait_) {
    port["ALU"] = std::deque<Uop>{};
    port["SFU"] = std::deque<Uop>{};
  }
  consumerReleaseStartOffset_ = static_cast<int>(uarch.consumerReleaseStartOffset);
}

void OoOCoreMainline::accept(const DynamicInst &inst) {
  Uop u;
  u.instId = inst.instId;
  u.op = inst.op;
  u.form = inst.form.empty() ? dtype_ : inst.form;
  const InstConfig &profile = db_.inst(u.op, u.form);
  u.opClass = profile.opClass;
  std::transform(u.opClass.begin(), u.opClass.end(), u.opClass.begin(),
                 [](unsigned char c) {
                   return static_cast<char>(std::toupper(c));
                 });
  if (u.opClass.empty())
    u.opClass = classifyOpClass(u.op, u.form);
  u.fuType = profile.exu.empty() ? "ALU" : profile.exu;
  std::transform(u.fuType.begin(), u.fuType.end(), u.fuType.begin(),
                 [](unsigned char c) {
                   return static_cast<char>(std::toupper(c));
                 });
  if (u.fuType != "ALU" && u.fuType != "SFU")
    u.fuType = "ALU";
  u.dispatchExu = profile.dispatchExu;
  std::transform(u.dispatchExu.begin(), u.dispatchExu.end(),
                 u.dispatchExu.begin(), [](unsigned char c) {
                   return static_cast<char>(std::toupper(c));
                 });
  u.latency = std::max<int64_t>(1, profile.latency);
  u.src = inst.src;
  u.dst = inst.dst;
  std::vector<std::string> releasedRatPregs;
  for (size_t sourceIndex = 0; sourceIndex < inst.src.size(); ++sourceIndex) {
    const auto &s = inst.src[sourceIndex];
    if (!isRegisterValue(s)) {
      u.pregSrc.push_back(std::nullopt);
      u.pregSrcGen.push_back(std::nullopt);
      continue;
    }
    auto it = rat_.find(s);
    if (it == rat_.end()) {
      u.pregSrc.push_back(std::nullopt);
      u.pregSrcGen.push_back(std::nullopt);
    } else {
      u.pregSrc.push_back(it->second);
      auto genIt = pregGeneration_.find(it->second);
      u.pregSrcGen.push_back(genIt == pregGeneration_.end()
                                 ? std::optional<int64_t>{0}
                                 : std::optional<int64_t>{genIt->second});
      if (sourceIndex < inst.srcValueRelease.size() &&
          inst.srcValueRelease[sourceIndex]) {
        releasedRatPregs.push_back(it->second);
        rat_.erase(it);
      }
    }
  }
  u.topBlockId = inst.topBlockId;
  u.iterStack = inst.iterStack;
  u.isLastInTopBlock = inst.isLastInTopBlock;
  u.streamSeq = inst.streamSeq;
  u.staticInstructionId = inst.staticInstructionId;
  u.iterationPath = inst.iterationPath;
  bindAlignState(u, inst);

  for (const auto &preg : u.pregSrc) {
    if (preg) {
      pregConsumerCount_[*preg] += 1;
      pregReleaseEligibleCycle_.erase(*preg);
    }
  }

  int allocCount = 0;
  for (size_t destinationIndex = 0; destinationIndex < u.dst.size();
       ++destinationIndex) {
    const auto &d = u.dst[destinationIndex];
    if (!isRegisterValue(d)) {
      u.pregDst.push_back(std::string{});
      continue;
    }
    std::string newPreg;
    if (theoreticalLimitMode_ || freelist_.empty()) {
      newPreg = "p" + std::to_string(nextDynamicPregId_++);
    } else {
      newPreg = freelist_.front();
      freelist_.pop_front();
    }
    std::string oldPreg;
    auto rit = rat_.find(d);
    if (rit != rat_.end())
      oldPreg = rit->second;
    const bool keepMapping =
        destinationIndex >= inst.dstValueKeep.size() ||
        inst.dstValueKeep[destinationIndex];
    if (keepMapping)
      rat_[d] = newPreg;
    u.pregDst.push_back(newPreg);
    u.pregOld.push_back(oldPreg.empty() ? std::optional<std::string>{} : std::optional<std::string>{oldPreg});
    pregGeneration_[newPreg] += 1;
    pregConsumerCount_[newPreg] = 0;
    pregPending_.insert(newPreg);
    pregReleaseEligibleCycle_.erase(newPreg);
    ++allocCount;
  }
  if (enableCreditVisibilityDelay_ && allocCount > 0)
    visiblePregFree_ = std::max(0, visiblePregFree_ - allocCount);

  if (enableShqCreditModel_ &&
      (u.opClass == "COMPUTE" || u.opClass == "STORE")) {
    ++shqUsed_;
    if (enableCreditVisibilityDelay_)
      ++visibleShqUsed_;
    u.isShqTracked = true;
  }

  if (u.opClass == "LOAD" || u.opClass == "STORE") {
    u.lsqReadyCycle = static_cast<int64_t>(cycle_) + oooToLsqDelay_;
    lsq_.push_back(u);
  } else {
    u.shqReadyCycle = static_cast<int64_t>(cycle_) + oooToShqDelay_;
    shq_.push_back(u);
  }
  rob_.push_back(u);

  for (const auto &releasedPreg : releasedRatPregs)
    (void)tryFreePreg(releasedPreg, cycle_);

  for (const auto &oldPreg : u.pregOld) {
    if (oldPreg)
      (void)tryFreePreg(*oldPreg, cycle_);
  }
}

void OoOCoreMainline::freeOldPregs(const Uop &u) {
  for (const auto &oldPreg : u.pregOld) {
    if (!oldPreg.has_value())
      continue;
    (void)tryFreePreg(*oldPreg, cycle_);
  }
}

void OoOCore::updateLsqReadyStates(int64_t cycle, bool storesOnly) {
  for (auto &u : lsq_) {
    if (u.state == "running" || u.state == "done")
      continue;
    if (storesOnly && u.opClass != "STORE")
      continue;
    if (u.opClass == "LOAD") {
      u.readyCycle = computeLoadReadyCycle(u);
    } else {
      auto ready = computeStoreReadyCycle(u);
      u.readyCycle = std::get<0>(ready);
      u.producerOpForStore = std::get<1>(ready);
      u.producerFormForStore = std::get<2>(ready);
      u.producerStartForStore = std::get<3>(ready);
      u.storeDependenciesResolved =
          u.readyCycle < 1000000000 &&
          (u.producerOpForStore.has_value() ||
           (u.alignStateOperation == "consume" && u.alignGeneration));
    }
    u.state = (cycle >= u.readyCycle) ? "ready" : "blocked";
  }
}

void OoOCore::issueReadyLsu(
    int64_t cycle, int &issuedLoads, int &issuedStores, int &issuedTotal,
    std::unordered_set<int64_t> &membarBlockedLoggedIds) {
  if (cycle < vfStartupCost_ || issuedTotal >= ubSlots_)
    return;

  struct Candidate {
    int classPriority = 0;
    int64_t streamSeq = 0;
    int64_t instId = 0;
  };
  const bool pregPressure =
      static_cast<int>(freelist_.size()) < lsuStorePriorityPregThreshold_;
  const std::string preferredClass = pregPressure ? "STORE" : "LOAD";
  std::vector<Candidate> candidates;
  for (const auto &u : lsq_) {
    if (u.state != "ready" ||
        (u.opClass != "LOAD" && u.opClass != "STORE"))
      continue;
    const int64_t age = u.streamSeq >= 0 ? u.streamSeq : u.instId;
    candidates.push_back(
        Candidate{u.opClass == preferredClass ? 0 : 1, age, u.instId});
  }
  std::sort(candidates.begin(), candidates.end(),
            [](const Candidate &lhs, const Candidate &rhs) {
              return std::tie(lhs.classPriority, lhs.streamSeq, lhs.instId) <
                     std::tie(rhs.classPriority, rhs.streamSeq, rhs.instId);
            });

  for (const auto &candidate : candidates) {
    if (issuedTotal >= ubSlots_)
      break;
    auto it = std::find_if(lsq_.begin(), lsq_.end(), [&](const Uop &u) {
      return u.instId == candidate.instId;
    });
    if (it == lsq_.end() || it->state != "ready")
      continue;
    Uop &u = *it;
    if (u.opClass == "LOAD" && issuedLoads >= loadPorts_)
      continue;
    if (u.opClass == "STORE" && issuedStores >= storePorts_)
      continue;
    if (blockedByControlUnit(u)) {
      if (membarBlockedLoggedIds.insert(u.instId).second)
        logMembarBlocked(u);
      continue;
    }
    if (u.opClass == "STORE" && !u.storeDependenciesResolved &&
        !u.producerOpForStore.has_value())
      continue;

    u.startCycle = cycle;
    u.blockedReason.reset();
    u.doneCycle = cycle + u.latency;
    u.state = "running";
    if (u.alignProducerRecord)
      u.alignProducerRecord->startCycle = cycle;
    scheduleSrcReleaseFromStart(u);
    if (u.opClass == "LOAD") {
      ++issuedLoads;
      for (const auto &pd : u.pregDst) {
        if (pd.empty())
          continue;
        pregProducer_[pd] = ProducerInfo{u.op, u.form, *u.startCycle, "LOAD"};
        pregPending_.erase(pd);
      }
    } else {
      ++issuedStores;
      if (u.isShqTracked) {
        scheduleShqRelease(cycle, 1);
        u.isShqTracked = false;
      }
    }
    ++issuedTotal;

    if (auto *robU = findRobUop(u.instId)) {
      robU->producerOpForStore = u.producerOpForStore;
      robU->producerFormForStore = u.producerFormForStore;
      robU->producerStartForStore = u.producerStartForStore;
      robU->storeDependenciesResolved = u.storeDependenciesResolved;
      robU->startCycle = u.startCycle;
      robU->doneCycle = u.doneCycle;
      robU->state = u.state;
      robU->isShqTracked = u.isShqTracked;
    }
    log("start", u);
    logStartSimple(u);
    lsq_.erase(it);
  }
}

void OoOCoreMainline::step() {
  const int64_t c = cycle_;

  runShqReleaseEvents(c);
  runSrcReleaseEvents(c);

  for (auto &u : rob_) {
    if (u.state == "running" && u.doneCycle.has_value() && c >= *u.doneCycle) {
      u.state = "done";
      if (u.alignProducerRecord)
        u.alignProducerRecord->doneCycle = u.doneCycle;
      if (u.exuPort >= 0 && u.exuPort < static_cast<int>(exqInflight_.size()))
        exqInflight_[static_cast<size_t>(u.exuPort)] = std::max(0, exqInflight_[static_cast<size_t>(u.exuPort)] - 1);
      log("done", u);
      logDoneSimple(u);
      lastDoneCycle_ = std::max(lastDoneCycle_, *u.doneCycle);
      for (const auto &pd : u.pregDst) {
        if (!pd.empty())
          (void)tryFreePreg(pd, c);
      }
    }
  }

  while (!rob_.empty() && rob_.front().state == "done") {
    Uop u = rob_.front();
    rob_.pop_front();
    freeOldPregs(u);
    log("retire", u);
  }

  tryFreeEligiblePregs(c);

  updateLsqReadyStates(c);
  for (auto &u : shq_) {
    if (u.state == "running" || u.state == "done")
      continue;
    int64_t t = std::max<int64_t>(vfStartupCost_, u.shqReadyCycle);
    for (const auto &preg : u.pregSrc) {
      if (!preg.has_value())
        continue;
      auto it = pregProducer_.find(*preg);
      if (it == pregProducer_.end()) {
        if (pregPending_.count(*preg))
          t = std::max<int64_t>(t, 1000000000);
        continue;
      }
      t = std::max<int64_t>(
          t, computeReadyTimeForSrc(it->second, u.op, u.form));
    }
    u.readyCycle = t;
    u.state = (c >= u.readyCycle) ? "ready" : "blocked";
  }

  int issuedLoads = 0;
  int issuedStores = 0;
  int issuedLsuTotal = 0;
  std::unordered_set<int64_t> membarBlockedLoggedIds;
  issueReadyLsu(c, issuedLoads, issuedStores, issuedLsuTotal,
                membarBlockedLoggedIds);

  for (auto &u : shq_) {
    if (u.state == "running" || u.state == "done")
      continue;
    int64_t t = std::max<int64_t>(vfStartupCost_, u.shqReadyCycle);
    for (const auto &preg : u.pregSrc) {
      if (!preg.has_value())
        continue;
      auto it = pregProducer_.find(*preg);
      if (it == pregProducer_.end()) {
        if (pregPending_.count(*preg))
          t = std::max<int64_t>(t, 1000000000);
        continue;
      }
      t = std::max<int64_t>(
          t, computeReadyTimeForSrc(it->second, u.op, u.form));
    }
    u.readyCycle = t;
    u.state = (c >= u.readyCycle) ? "ready" : "blocked";
  }

  std::vector<bool> exuUsedThisCycle(static_cast<size_t>(issuePorts_), false);
  std::unordered_set<std::string> issuedSrcsThisCycle;
  if (!enableIsuQueueModel_) {
    int ex = 0;
    for (auto it = shq_.begin(); it != shq_.end();) {
      auto &u = *it;
      if (u.state != "ready" || u.opClass != "COMPUTE") {
        ++it;
        continue;
      }
      if (ex >= issuePorts_)
        break;
      if (enforceSameCycleSrcHazard_ && !theoreticalLimitMode_) {
        bool hazard = false;
        for (const auto &ps : u.pregSrc) {
          if (ps && issuedSrcsThisCycle.count(*ps)) {
            hazard = true;
            break;
          }
        }
        if (hazard) {
          ++it;
          continue;
        }
      }
      const std::string &fuType = u.fuType;
      const std::vector<int> legalPorts = eligibleExuPorts(u);
      int chosenPort = -1;
      for (int port : legalPorts) {
        if (port < 0 || port >= issuePorts_ || exuUsedThisCycle[static_cast<size_t>(port)])
          continue;
        const std::string *prevOp = enableCrossFuIi_ ? &lastOpExu_[static_cast<size_t>(port)]
                                                     : (fuType == "SFU" ? &lastOpSFU_[static_cast<size_t>(port)]
                                                                        : &lastOpALU_[static_cast<size_t>(port)]);
        const std::string *prevForm =
            enableCrossFuIi_
                ? &lastFormExu_[static_cast<size_t>(port)]
                : (fuType == "SFU"
                       ? &lastFormSFU_[static_cast<size_t>(port)]
                       : &lastFormALU_[static_cast<size_t>(port)]);
        const int64_t prevIssue = enableCrossFuIi_ ? lastIssueCycleExu_[static_cast<size_t>(port)]
                                                   : (fuType == "SFU" ? lastIssueCycleSFU_[static_cast<size_t>(port)]
                                                                      : lastIssueCycleALU_[static_cast<size_t>(port)]);
        if (c >= prevIssue + getIi(prevOp, prevForm, u.op, u.form)) {
          chosenPort = port;
          break;
        }
      }
      if (chosenPort < 0) {
        ++it;
        continue;
      }
      u.startCycle = c;
      u.doneCycle = c + u.latency;
      u.state = "running";
      u.exuPort = chosenPort;
      scheduleSrcReleaseFromStart(u);
      if (auto *robU = findRobUop(u.instId)) {
        robU->startCycle = u.startCycle;
        robU->doneCycle = u.doneCycle;
        robU->state = u.state;
        robU->exuPort = u.exuPort;
      }
      log("start", u);
      logStartSimple(u);
      ++ex;
      exuUsedThisCycle[static_cast<size_t>(chosenPort)] = true;
      for (const auto &ps : u.pregSrc)
        if (ps)
          issuedSrcsThisCycle.insert(*ps);
      if (enableCrossFuIi_) {
        lastIssueCycleExu_[static_cast<size_t>(chosenPort)] = c;
        lastOpExu_[static_cast<size_t>(chosenPort)] = u.op;
        lastFormExu_[static_cast<size_t>(chosenPort)] = u.form;
      } else if (fuType == "SFU") {
        lastIssueCycleSFU_[static_cast<size_t>(chosenPort)] = c;
        lastOpSFU_[static_cast<size_t>(chosenPort)] = u.op;
        lastFormSFU_[static_cast<size_t>(chosenPort)] = u.form;
      } else {
        lastIssueCycleALU_[static_cast<size_t>(chosenPort)] = c;
        lastOpALU_[static_cast<size_t>(chosenPort)] = u.op;
        lastFormALU_[static_cast<size_t>(chosenPort)] = u.form;
      }
      exqInflight_[static_cast<size_t>(chosenPort)] += 1;
      for (const auto &pd : u.pregDst) {
        if (!pd.empty()) {
          pregProducer_[pd] =
              ProducerInfo{u.op, u.form, *u.startCycle, "COMPUTE"};
          pregPending_.erase(pd);
        }
      }
      it = shq_.erase(it);
    }
  } else {
    std::vector<int> shqToExqCnt(static_cast<size_t>(issuePorts_), 0);
    int exCount = 0;
    const bool useFuRrFifo = useFuRoundRobinFifo();
    std::unordered_set<std::string> blockedFuTypes;
    for (auto it = shq_.begin(); it != shq_.end();) {
      auto &u = *it;
      const std::string &fuType = u.fuType;
      if (useFuRrFifo && blockedFuTypes.count(fuType)) {
        ++it;
        continue;
      }
      if (u.state != "ready") {
        ++it;
        continue;
      }
      if (exCount >= issuePorts_)
        break;
      bool hazard = false;
      if (enforceSameCycleSrcHazard_ && !theoreticalLimitMode_) {
        for (const auto &ps : u.pregSrc) {
          if (ps && issuedSrcsThisCycle.count(*ps)) {
            hazard = true;
            break;
          }
        }
      }
      if (hazard) {
        if (useFuRrFifo)
          blockedFuTypes.insert(fuType);
        ++it;
        continue;
      }
      const std::vector<int> legalPorts = eligibleExuPorts(u);
      std::vector<int> candidates;
      for (int port : legalPorts) {
        if (port < 0 || port >= issuePorts_)
          continue;
        if (shqToExqCnt[static_cast<size_t>(port)] >= shqToExqPortPerCycle_)
          continue;
        const auto &q = exqWait_[static_cast<size_t>(port)];
        int occ = static_cast<int>(q.at("ALU").size() + q.at("SFU").size());
        if (exqCapacityCountsInflight_)
          occ += exqInflight_[static_cast<size_t>(port)];
        if (occ >= exqDepth_)
          continue;
        candidates.push_back(port);
      }

      if (candidates.empty()) {
        bool allPortsLegal = true;
        for (int port = 0; port < issuePorts_; ++port) {
          if (std::find(legalPorts.begin(), legalPorts.end(), port) == legalPorts.end()) {
            allPortsLegal = false;
            break;
          }
        }
        if (useFuRrFifo && allPortsLegal)
          blockedFuTypes.insert(fuType);
        ++it;
        continue;
      }

      const size_t shqIndex = static_cast<size_t>(std::distance(shq_.begin(), it));
      if (useExu0Reserve() &&
          std::find(candidates.begin(), candidates.end(), 0) != candidates.end() &&
          legalPorts.size() > 1 &&
          u.dispatchExu != "EXU0_ONLY") {
        const int pressure = exu0OnlyPressureCount(shqIndex);
        if (pressure > 0 && candidates.size() > 1) {
          std::vector<int> occupancy(static_cast<size_t>(issuePorts_), 0);
          for (int port = 0; port < issuePorts_; ++port) {
            const auto &q = exqWait_[static_cast<size_t>(port)];
            occupancy[static_cast<size_t>(port)] =
                static_cast<int>(q.at("ALU").size() + q.at("SFU").size());
            if (exqCapacityCountsInflight_)
              occupancy[static_cast<size_t>(port)] +=
                  exqInflight_[static_cast<size_t>(port)];
          }

          auto balanceError = [&](int port) {
            std::vector<int> projected = occupancy;
            ++projected[static_cast<size_t>(port)];
            double nonExu0Average = 0.0;
            for (int other = 1; other < issuePorts_; ++other)
              nonExu0Average += projected[static_cast<size_t>(other)];
            nonExu0Average /= std::max(1, issuePorts_ - 1);
            return std::abs((nonExu0Average - projected[0]) - pressure);
          };

          double bestError = std::numeric_limits<double>::infinity();
          for (int port : candidates)
            bestError = std::min(bestError, balanceError(port));
          std::vector<int> balancedCandidates;
          std::copy_if(candidates.begin(), candidates.end(),
                       std::back_inserter(balancedCandidates),
                       [&](int port) { return balanceError(port) == bestError; });
          candidates = std::move(balancedCandidates);
        }
      }

      const int64_t recv = c + exqRecvDelay_;
      auto predictCandidate = [&](int port) {
        const auto &fq = exqWait_[static_cast<size_t>(port)].at(fuType);
        if (!fq.empty()) {
          const Uop &prev = fq.back();
          return std::max<int64_t>(
              recv, prev.exqPredIssue +
                        getIi(&prev.op, &prev.form, u.op, u.form));
        }
        return predictExqIssueCycle(port, fuType, u.op, u.form, recv);
      };

      int chosenPort = -1;
      int64_t chosenPred = 0;
      if (useFuRrFifo) {
        chosenPort = selectFuRoundRobinPort(fuType, candidates);
        chosenPred = predictCandidate(chosenPort);
      } else {
        int chosenOcc = 0;
        for (int port : candidates) {
          const auto &q = exqWait_[static_cast<size_t>(port)];
          int occ = static_cast<int>(q.at("ALU").size() + q.at("SFU").size());
          if (exqCapacityCountsInflight_)
            occ += exqInflight_[static_cast<size_t>(port)];
          const int64_t pred = predictCandidate(port);
          const auto key = std::make_tuple(pred, occ, port);
          const auto best = std::make_tuple(chosenPred, chosenOcc, chosenPort);
          if (chosenPort < 0 || key < best) {
            chosenPort = port;
            chosenPred = pred;
            chosenOcc = occ;
          }
        }
      }
      u.exuPort = chosenPort;
      u.exqRecvCycle = c + exqRecvDelay_;
      u.exqPredIssue = chosenPred;
      u.state = "exq_wait";
      if (u.opClass == "COMPUTE" || u.opClass == "STORE") {
        scheduleShqRelease(c, 1);
        u.isShqTracked = false;
        if (auto *robU = findRobUop(u.instId))
          robU->isShqTracked = false;
      }
      exqWait_[static_cast<size_t>(chosenPort)][fuType].push_back(u);
      shqToExqCnt[static_cast<size_t>(chosenPort)] += 1;
      ++exCount;
      for (const auto &ps : u.pregSrc)
        if (ps)
          issuedSrcsThisCycle.insert(*ps);
      it = shq_.erase(it);
    }

    for (int port = 0; port < issuePorts_; ++port) {
      if (exuUsedThisCycle[static_cast<size_t>(port)])
        continue;
      auto &q = exqWait_[static_cast<size_t>(port)];
      std::string bestFu;
      Uop *bestU = nullptr;
      std::tuple<int64_t, int64_t, int64_t> bestKey{0, 0, 0};
      for (const std::string &fuType : {std::string("ALU"), std::string("SFU")}) {
        auto &fq = q[fuType];
        if (fq.empty())
          continue;
        Uop &cand = fq.front();
        if (exqIssueInflightCapPerPort_ > 0 &&
            exqInflight_[static_cast<size_t>(port)] >= exqIssueInflightCapPerPort_)
          continue;
        if (cand.exqRecvCycle > c)
          continue;
        int64_t ready = std::max<int64_t>(vfStartupCost_, cand.shqReadyCycle);
        bool pending = false;
        for (const auto &preg : cand.pregSrc) {
          if (!preg)
            continue;
          auto pit = pregProducer_.find(*preg);
          if (pit == pregProducer_.end()) {
            if (pregPending_.count(*preg))
              pending = true;
            continue;
          }
          ready = std::max<int64_t>(
              ready,
              computeReadyTimeForSrc(pit->second, cand.op, cand.form));
        }
        if (pending || ready > c)
          continue;
        const int64_t ii =
            getIi(&lastOpExu_[static_cast<size_t>(port)],
                  &lastFormExu_[static_cast<size_t>(port)], cand.op,
                  cand.form);
        if (c < lastIssueCycleExu_[static_cast<size_t>(port)] + ii)
          continue;
        auto key = std::make_tuple(ready, cand.exqRecvCycle, cand.instId);
        if (!bestU || key < bestKey) {
          bestFu = fuType;
          bestU = &cand;
          bestKey = key;
        }
      }
      if (!bestU)
        continue;
      Uop u = *bestU;
      q[bestFu].pop_front();
      u.startCycle = c;
      u.doneCycle = c + u.latency;
      u.state = "running";
      u.exuPort = port;
      scheduleSrcReleaseFromStart(u);
      if (auto *robU = findRobUop(u.instId)) {
        robU->startCycle = u.startCycle;
        robU->doneCycle = u.doneCycle;
        robU->state = u.state;
        robU->exuPort = u.exuPort;
      }
      log("start", u);
      logStartSimple(u);
      lastIssueCycleExu_[static_cast<size_t>(port)] = c;
      lastOpExu_[static_cast<size_t>(port)] = u.op;
      lastFormExu_[static_cast<size_t>(port)] = u.form;
      if (bestFu == "SFU") {
        lastIssueCycleSFU_[static_cast<size_t>(port)] = c;
        lastOpSFU_[static_cast<size_t>(port)] = u.op;
        lastFormSFU_[static_cast<size_t>(port)] = u.form;
      } else {
        lastIssueCycleALU_[static_cast<size_t>(port)] = c;
        lastOpALU_[static_cast<size_t>(port)] = u.op;
        lastFormALU_[static_cast<size_t>(port)] = u.form;
      }
      exuUsedThisCycle[static_cast<size_t>(port)] = true;
      exqInflight_[static_cast<size_t>(port)] += 1;
      for (const auto &pd : u.pregDst) {
        if (!pd.empty()) {
          pregProducer_[pd] =
              ProducerInfo{u.op, u.form, *u.startCycle, "COMPUTE"};
          pregPending_.erase(pd);
        }
      }
    }
  }

  updateLsqReadyStates(c, true);
  issueReadyLsu(c, issuedLoads, issuedStores, issuedLsuTotal,
                membarBlockedLoggedIds);

  ++cycle_;
}

} // namespace vfsim
