// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/OOO.h"

#include "native/ISATraits.h"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

bool isIntermediateMemName(const std::string &name) {
  std::string lower = name;
  std::transform(lower.begin(), lower.end(), lower.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return lower.rfind("mem_inter", 0) == 0;
}

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

} // namespace

OoOCore::OoOCore(const UarchConfig &uarch, const ParamDB &db, std::string dtype,
                 const std::unordered_map<std::string, ValueInfo> &values)
    : db_(db), dtype_(std::move(dtype)), valueStorage_(values) {
  theoreticalLimitMode_ = false;
  enableIsuQueueModel_ = uarch.enableIsuQueueModel;
  loadPorts_ = static_cast<int>(uarch.loadPorts);
  issuePorts_ = static_cast<int>(uarch.issuePorts);
  storePorts_ = static_cast<int>(uarch.storePorts);
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
  loadDoneLatency_ = static_cast<int>(uarch.loadDoneLatency ? uarch.loadDoneLatency : 9);
  oooToShqDelay_ = static_cast<int>(uarch.oooToShqDelay ? uarch.oooToShqDelay : 1);
  oooToLsqDelay_ = static_cast<int>(uarch.oooToLsqDelay ? uarch.oooToLsqDelay : 1);
  exqRecvDelay_ = static_cast<int>(uarch.exqRecvDelay ? uarch.exqRecvDelay : 1);
  memBarStrong_ = uarch.memBarMode == "strong";
  enforceSameCycleSrcHazard_ = uarch.enforceSameCycleSrcHazard;
  enableExqGreedyBalance_ = false;
  enableShqCreditModel_ = uarch.enableShqCreditModel;
  enableCreditVisibilityDelay_ = uarch.enableCreditVisibilityDelay;
  enableCrossFuIi_ = uarch.enableCrossFuIi;
  exqCapacityCountsInflight_ = uarch.exqCapacityCountsInflight;
  exqDepth_ = static_cast<int>(uarch.exqDepth ? uarch.exqDepth : 26);
  shqToExqPortPerCycle_ = static_cast<int>(uarch.shqToExqPortPerCycle ? uarch.shqToExqPortPerCycle : 1);
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

bool OoOCore::isUBValue(const std::string &name) const {
  return valueStorage_.isUB(name);
}

int64_t OoOCore::computeReadyTimeForSrc(
    const ProducerInfo &producerInfo, const std::string &consumerOp,
    const std::string &consumerForm) const {
  const int64_t fwd =
      db_.forwardingCycles(producerInfo.op, producerInfo.form, consumerOp,
                           consumerForm);
  if (isComputeOp(db_, consumerOp, consumerForm) && enableIsuQueueModel_)
    return producerInfo.startCycle + std::max<int64_t>(0, fwd - 1);
  return producerInfo.startCycle + fwd;
}

int64_t OoOCore::computeLoadReadyCycle(const Uop &u) const {
  int64_t t = std::max<int64_t>(vfStartupCost_, u.lsqReadyCycle);
  for (auto *pred : u.memDepUops) {
    if (pred && pred->doneCycle.has_value())
      t = std::max<int64_t>(t, pred->doneCycle.value());
  }
  if (memBarStrong_) {
    for (const auto &s : u.src) {
      if (!isIntermediateMemName(s))
        continue;
      if (u.topBlockId <= 0)
        continue;
      const auto it = blockReleaseCycle_.find(u.topBlockId - 1);
      if (it == blockReleaseCycle_.end())
        return 1000000000;
      t = std::max<int64_t>(t, it->second);
    }
  }
  return t;
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
  if (bestT < 0)
    return {1000000000, std::nullopt, std::nullopt, std::nullopt};
  bestT = std::max<int64_t>(bestT, u.lsqReadyCycle);
  return {bestT, pop, pform, pst};
}

int64_t OoOCore::dataStoreCost(const std::string &producerOp,
                               const std::string &producerForm) const {
  const auto &cfg = db_.inst(producerOp, producerForm);
  return cfg.dataStoreCost > 0 ? cfg.dataStoreCost : 1;
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
  if (tag == "EXU01")
    return issuePorts_ >= 2 ? std::vector<int>{0, 1} : std::vector<int>{0};
  if (tag == "EXU012")
    return issuePorts_ >= 3 ? std::vector<int>{0, 1, 2} : std::vector<int>{0, 1};
  std::vector<int> out;
  for (int i = 0; i < issuePorts_; ++i)
    out.push_back(i);
  return out;
}

int64_t OoOCore::getIi(const std::string *prevOp,
                       const std::string *prevForm,
                       const std::string &curOp,
                       const std::string &curForm) const {
  if (!prevOp || !prevForm || prevOp->empty())
    return 1;
  return db_.initiationInterval(*prevOp, *prevForm, curOp, curForm);
}

void OoOCore::log(const std::string &event, const Uop &u) {
  history_.push_back(HistoryRecord{
      cycle_, event, u.instId, u.op, u.state, u.readyCycle, u.startCycle,
      u.doneCycle, u.src, u.dst, u.pregSrc, u.pregDst, u.pregOld,
      u.producerOpForStore, u.producerStartForStore});
}

void OoOCore::logStartSimple(const Uop &u) {
  startLogs_.push_back(SimpleLogRecord{cycle_, u.instId, u.op, u.dst, u.src});
}

void OoOCore::logDoneSimple(const Uop &u) {
  doneLogs_.push_back(SimpleLogRecord{u.doneCycle.value_or(cycle_), u.instId, u.op, u.dst, u.src});
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
       << "\"ready\":" << h.ready << ","
       << "\"start\":" << (h.start ? std::to_string(*h.start) : "null") << ","
       << "\"done\":" << (h.done ? std::to_string(*h.done) : "null") << ","
       << "\"src\":" << joinJsonArray(h.src) << ","
       << "\"dst\":" << joinJsonArray(h.dst) << ","
       << "\"preg_src\":" << joinJsonArray(h.pregSrc) << ","
       << "\"preg_dst\":" << joinJsonArray(h.pregDst) << ","
       << "\"preg_old\":" << joinJsonArray(h.pregOld) << ","
       << "\"producer_op_for_store\":" << (h.producerOpForStore ? "\"" + jsonEscape(*h.producerOpForStore) + "\"" : "null") << ","
       << "\"producer_start_for_store\":" << (h.producerStartForStore ? std::to_string(*h.producerStartForStore) : "null")
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
      << "\",\"dst\":" << joinJsonArray(r.dst) << ",\"src\":" << joinJsonArray(r.src) << "}\n";
  }
  std::ofstream d(donePath);
  for (const auto &r : doneLogs_) {
    d << "{\"cy\":" << r.cy << ",\"inst_id\":" << r.instId << ",\"op\":\"" << jsonEscape(r.op)
      << "\",\"dst\":" << joinJsonArray(r.dst) << ",\"src\":" << joinJsonArray(r.src) << "}\n";
  }
}

Uop *OoOCore::findRobUop(int64_t instId) {
  for (auto &u : rob_) {
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
  u.src = inst.src;
  u.dst = inst.dst;
  for (const auto &s : inst.src) {
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
    }
  }
  u.topBlockId = inst.topBlockId;
  u.iterStack = inst.iterStack;
  u.isLastInTopBlock = inst.isLastInTopBlock;

  for (const auto &preg : u.pregSrc) {
    if (preg) {
      pregConsumerCount_[*preg] += 1;
      pregReleaseEligibleCycle_.erase(*preg);
    }
  }

  int allocCount = 0;
  for (const auto &d : u.dst) {
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

  if (enableShqCreditModel_ && usesSharedShqCredit(db_, u.op, u.form)) {
    ++shqUsed_;
    if (enableCreditVisibilityDelay_)
      ++visibleShqUsed_;
    u.isShqTracked = true;
  }

  if (usesLsq(db_, u.op, u.form)) {
    u.lsqReadyCycle = static_cast<int64_t>(cycle_) + oooToLsqDelay_;
    lsq_.push_back(u);
  } else {
    u.shqReadyCycle = static_cast<int64_t>(cycle_) + oooToShqDelay_;
    shq_.push_back(u);
  }
  rob_.push_back(u);

  if (isStoreOp(db_, u.op, u.form))
    blockOutstandingStores_[u.topBlockId] += 1;

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

void OoOCoreMainline::step() {
  const int64_t c = cycle_;

  runShqReleaseEvents(c);
  runSrcReleaseEvents(c);

  for (auto &u : rob_) {
    if (u.state == "running" && u.doneCycle.has_value() && c >= *u.doneCycle) {
      u.state = "done";
      if (u.exuPort >= 0 && u.exuPort < static_cast<int>(exqInflight_.size()))
        exqInflight_[static_cast<size_t>(u.exuPort)] = std::max(0, exqInflight_[static_cast<size_t>(u.exuPort)] - 1);
      if (u.isLastInTopBlock)
        blockLastInstDone_[u.topBlockId] = true;
      if (isStoreOp(db_, u.op, u.form)) {
        auto it = blockOutstandingStores_.find(u.topBlockId);
        if (it != blockOutstandingStores_.end())
          it->second = std::max(0, it->second - 1);
      }
      if (blockLastInstDone_[u.topBlockId] &&
          blockOutstandingStores_[u.topBlockId] == 0) {
        auto prev = blockReleaseCycle_.find(u.topBlockId);
        if (prev == blockReleaseCycle_.end())
          blockReleaseCycle_[u.topBlockId] = *u.doneCycle;
        else
          prev->second = std::max<int64_t>(prev->second, *u.doneCycle);
      }
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

  for (auto &u : lsq_) {
    if (u.state == "running" || u.state == "done")
      continue;
    if (isLoadOp(db_, u.op, u.form))
      u.readyCycle = computeLoadReadyCycle(u);
    else
      u.readyCycle = std::get<0>(computeStoreReadyCycle(u));
    u.state = (c >= u.readyCycle) ? "ready" : "blocked";
  }
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

  int ld = 0;
  for (auto it = lsq_.begin(); it != lsq_.end();) {
    auto &u = *it;
    if (u.state != "ready" || !isLoadOp(db_, u.op, u.form)) {
      ++it;
      continue;
    }
    if (ld >= loadPorts_)
      break;
    u.startCycle = c;
    u.doneCycle = c + loadDoneLatency_;
    u.state = "running";
    scheduleSrcReleaseFromStart(u);
    if (auto *robU = findRobUop(u.instId)) {
      robU->startCycle = u.startCycle;
      robU->doneCycle = u.doneCycle;
      robU->state = u.state;
    }
    log("start", u);
    logStartSimple(u);
    ++ld;
    for (const auto &pd : u.pregDst) {
      if (!pd.empty()) {
        pregProducer_[pd] =
            ProducerInfo{u.op, u.form, *u.startCycle, "LOAD"};
        pregPending_.erase(pd);
      }
    }
    it = lsq_.erase(it);
  }

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
      if (u.state != "ready" || isLoadOp(db_, u.op, u.form) ||
          isStoreOp(db_, u.op, u.form)) {
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
      const std::string fuType = getFuType(u.op, u.form);
      const std::vector<int> legalPorts = eligibleExuPorts(u.op, u.form);
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
      u.doneCycle = c + std::max<int64_t>(1, db_.inst(u.op, u.form).latency);
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
    for (auto it = shq_.begin(); it != shq_.end();) {
      auto &u = *it;
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
        ++it;
        continue;
      }
      const std::string fuType = getFuType(u.op, u.form);
      const auto legalPorts = eligibleExuPorts(u.op, u.form);
      int chosenPort = -1;
      int64_t chosenPred = 0;
      int chosenOcc = 0;
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
        const int64_t recv = c + exqRecvDelay_;
        int64_t pred = recv;
        const auto &fq = q.at(fuType);
        if (!fq.empty()) {
          const Uop &prev = fq.back();
          pred = std::max<int64_t>(
              pred, prev.exqPredIssue +
                        getIi(&prev.op, &prev.form, u.op, u.form));
        } else {
          pred = std::max<int64_t>(
              pred, predictExqIssueCycle(port, fuType, u.op, u.form, recv));
        }
        const auto key = std::make_tuple(pred, occ, port);
        const auto best = std::make_tuple(chosenPred, chosenOcc, chosenPort);
        if (chosenPort < 0 || key < best) {
          chosenPort = port;
          chosenPred = pred;
          chosenOcc = occ;
        }
      }
      if (chosenPort < 0) {
        ++it;
        continue;
      }
      u.exuPort = chosenPort;
      u.exqRecvCycle = c + exqRecvDelay_;
      u.exqPredIssue = chosenPred;
      u.state = "exq_wait";
      if (usesSharedShqCredit(db_, u.op, u.form)) {
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
      u.doneCycle = c + std::max<int64_t>(1, db_.inst(u.op, u.form).latency);
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

  int st = 0;
  for (auto it = lsq_.begin(); it != lsq_.end();) {
    auto &u = *it;
    if (u.state != "ready" || !isStoreOp(db_, u.op, u.form)) {
      ++it;
      continue;
    }
    if (st >= storePorts_)
      break;
    auto ready = computeStoreReadyCycle(u);
    if (c < std::get<0>(ready)) {
      ++it;
      continue;
    }
    u.producerOpForStore = std::get<1>(ready);
    u.producerFormForStore = std::get<2>(ready);
    u.producerStartForStore = std::get<3>(ready);
    if (!u.producerOpForStore.has_value()) {
      ++it;
      continue;
    }
    u.startCycle = c;
    u.doneCycle =
        c + dataStoreCost(*u.producerOpForStore,
                          u.producerFormForStore.value_or(u.form));
    u.state = "running";
    scheduleSrcReleaseFromStart(u);
    if (usesSharedShqCredit(db_, u.op, u.form)) {
      scheduleShqRelease(c, 1);
      u.isShqTracked = false;
    }
    if (auto *robU = findRobUop(u.instId)) {
      robU->producerOpForStore = u.producerOpForStore;
      robU->producerStartForStore = u.producerStartForStore;
      robU->startCycle = u.startCycle;
      robU->doneCycle = u.doneCycle;
      robU->state = u.state;
      robU->isShqTracked = false;
    }
    log("start", u);
    logStartSimple(u);
    ++st;
    it = lsq_.erase(it);
  }

  ++cycle_;
}

} // namespace vfsim
