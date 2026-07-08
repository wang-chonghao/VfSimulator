// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_OOO_H
#define VFSIM_NATIVE_OOO_H

#include "native/IDU.h"

#include <deque>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace vfsim {

struct Uop {
  int64_t instId = 0;
  std::string op;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::vector<std::optional<std::string>> pregSrc;
  std::vector<std::optional<int64_t>> pregSrcGen;
  std::vector<std::string> pregDst;
  std::vector<std::optional<std::string>> pregOld;

  std::string state = "blocked";
  int64_t readyCycle = 0;
  std::optional<int64_t> startCycle;
  std::optional<int64_t> doneCycle;

  std::optional<std::string> producerOpForStore;
  std::optional<int64_t> producerStartForStore;
  std::vector<Uop *> memDepUops;
  int64_t topBlockId = 0;
  std::vector<int64_t> iterStack;
  bool isLastInTopBlock = false;
  int exuPort = -1;
  int64_t shqReadyCycle = 0;
  int64_t lsqReadyCycle = 0;
  bool isShqTracked = false;
  int64_t exqRecvCycle = 0;
  int64_t exqPredIssue = 0;
};

struct SrcReleaseEvent {
  int64_t instId = 0;
  std::string preg;
  int64_t gen = 0;
};

struct HistoryRecord {
  int64_t cy = 0;
  std::string event;
  int64_t id = 0;
  std::string op;
  std::string state;
  int64_t ready = 0;
  std::optional<int64_t> start;
  std::optional<int64_t> done;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::vector<std::optional<std::string>> pregSrc;
  std::vector<std::string> pregDst;
  std::vector<std::optional<std::string>> pregOld;
  std::optional<std::string> producerOpForStore;
  std::optional<int64_t> producerStartForStore;
};

struct SimpleLogRecord {
  int64_t cy = 0;
  int64_t instId = 0;
  std::string op;
  std::vector<std::string> dst;
  std::vector<std::string> src;
};

class OoOCore {
public:
  OoOCore(const UarchConfig &uarch, const ParamDB &db, std::string dtype = "fp32");
  virtual ~OoOCore() = default;

  int getFreePreg() const;
  int getFreeShqQueue() const;
  int getFreeLsq() const;
  int getFreeShq() const;
  int getRobSize() const noexcept { return static_cast<int>(rob_.size()); }
  int getLsqSize() const noexcept { return static_cast<int>(lsq_.size()); }
  int getShqSize() const noexcept { return static_cast<int>(shq_.size()); }
  virtual std::unordered_map<std::string, int> updateIduVisibility(int64_t cycle);

  virtual void accept(const DynamicInst &inst) = 0;
  virtual void step() = 0;
  virtual int64_t vfEndCycle() const;
  virtual void dumpHistory(const std::string &path) const;
  virtual void dumpSimpleLogs(const std::string &startPath,
                              const std::string &donePath) const;

protected:
  const ParamDB &db_;
  std::string dtype_;
  bool theoreticalLimitMode_ = false;
  bool enableIsuQueueModel_ = false;
  int loadPorts_ = 2;
  int issuePorts_ = 2;
  int storePorts_ = 1;
  int shqDepth_ = 58;
  int lsqDepth_ = 24;
  int pregNum_ = 68;
  int vfStartupCost_ = 0;
  int vfDrainCost_ = 0;
  int64_t cycle_ = 0;
  int64_t lastDoneCycle_ = 0;

  std::deque<std::string> freelist_;
  std::unordered_map<std::string, std::string> rat_;
  int64_t nextDynamicPregId_ = 0;

  std::deque<Uop> shq_;
  std::deque<Uop> lsq_;
  std::deque<Uop> rob_;

  std::unordered_map<std::string, std::tuple<std::string, int64_t, std::string>> pregProducer_;
  std::vector<int64_t> lastIssueCycleALU_;
  std::vector<int64_t> lastIssueCycleSFU_;
  std::vector<std::string> lastOpALU_;
  std::vector<std::string> lastOpSFU_;
  std::vector<int64_t> lastIssueCycleExu_;
  std::vector<std::string> lastOpExu_;
  std::vector<int> exqInflight_;

  int loadDoneLatency_ = 9;
  int oooToShqDelay_ = 1;
  int oooToLsqDelay_ = 1;
  int exqRecvDelay_ = 1;
  bool memBarStrong_ = false;
  bool enforceSameCycleSrcHazard_ = true;
  bool enableExqGreedyBalance_ = false;
  bool enableShqCreditModel_ = false;
  bool enableCreditVisibilityDelay_ = false;
  bool enableCrossFuIi_ = false;
  bool exqCapacityCountsInflight_ = false;
  int exqDepth_ = 26;
  int shqToExqPortPerCycle_ = 1;
  int exqIssueInflightCapPerPort_ = 0;
  int computeInflightCap_ = 0;
  int shqReleaseDelay_ = 1;
  int iduVisiblePregDelay_ = 0;
  int iduVisibleShqDelay_ = 0;
  int visiblePregFree_ = 0;
  int visibleShqUsed_ = 0;
  int shqUsed_ = 0;
  std::unordered_map<int64_t, int> visiblePregFreeEvents_;
  std::unordered_map<int64_t, int> visibleShqReleaseEvents_;
  std::unordered_map<int64_t, int> shqReleaseEvents_;
  std::unordered_map<int64_t, int> blockOutstandingStores_;
  std::unordered_map<int64_t, bool> blockLastInstDone_;
  std::unordered_map<int64_t, int64_t> blockReleaseCycle_;
  int iduMailboxPregReleaseDelta_ = 0;
  int iduMailboxShqReleaseDelta_ = 0;
  int consumerReleaseStartOffset_ = 0;
  std::unordered_map<std::string, int> pregConsumerCount_;
  std::unordered_set<std::string> pregPending_;
  std::unordered_map<std::string, int64_t> pregReleaseEligibleCycle_;
  std::unordered_map<std::string, int64_t> pregGeneration_;
  std::unordered_map<int64_t, std::vector<SrcReleaseEvent>> srcReleaseEvents_;
  std::unordered_map<int64_t, int> srcReleaseExpected_;
  std::unordered_map<int64_t, int> srcReleaseSeen_;
  std::unordered_set<int64_t> srcReleaseScheduledInstIds_;

  std::vector<HistoryRecord> history_;
  std::vector<SimpleLogRecord> startLogs_;
  std::vector<SimpleLogRecord> doneLogs_;

  virtual std::string classifyOpClass(const std::string &op) const;
  int64_t computeReadyTimeForSrc(const std::tuple<std::string, int64_t, std::string> &producerInfo,
                                 const std::string &consumerOp) const;
  int64_t computeLoadReadyCycle(const Uop &u) const;
  std::tuple<int64_t, std::optional<std::string>, std::optional<int64_t>>
  computeStoreReadyCycle(const Uop &u) const;
  int64_t dataStoreCost(const std::string &producerOp) const;
  std::string getFuType(const std::string &op) const;
  std::vector<int> eligibleExuPorts(const std::string &op) const;
  int64_t getIi(const std::string *prevOp, const std::string &curOp) const;
  void log(const std::string &event, const Uop &u);
  void logStartSimple(const Uop &u);
  void logDoneSimple(const Uop &u);
  Uop *findRobUop(int64_t instId);
  bool isCurrentMapping(const std::string &preg) const;
  void scheduleSrcReleaseFromStart(const Uop &u);
  void runSrcReleaseEvents(int64_t cycle);
  bool tryFreePreg(const std::string &preg, int64_t cycle);
  void tryFreeEligiblePregs(int64_t cycle);
  int exqOccupancy(int port) const;
  int totalComputeInflight() const;
  int64_t predictExqIssueCycle(int port, const std::string &fuType,
                               const std::string &op, int64_t recvCycle) const;
  void scheduleShqRelease(int64_t cycle, int count = 1);
  void runShqReleaseEvents(int64_t cycle);

  virtual void freeOldPregs(const Uop &u) = 0;
};

class OoOCoreMainline final : public OoOCore {
public:
  OoOCoreMainline(const UarchConfig &uarch, const ParamDB &db, std::string dtype = "fp32");

  void accept(const DynamicInst &inst) override;
  void step() override;

private:
  std::vector<int> exqInflightPerPort_;
  std::vector<std::unordered_map<std::string, std::deque<Uop>>> exqWait_;

  void freeOldPregs(const Uop &u) override;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_OOO_H
