// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. Please read the License for details.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/SimulatorRunner.h"

#include "native/ISATraits.h"
#include "native/ProgramCanonicalization.h"
#include "native/ProgramFlatten.h"
#include "native/ProgramVregLiveRangeNormalization.h"
#include "native/ValueStorage.h"

#include <deque>
#include <filesystem>
#include <fstream>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>

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

template <typename T>
void dumpJsonLines(const std::vector<T> &records, const std::string &path) {
  std::ofstream os(path);
  for (const auto &r : records) {
    os << r << '\n';
  }
}

struct Reservation {
  int64_t preg = 0;
  int64_t shqQueue = 0;
  int64_t lsq = 0;
  int64_t shq = 0;
};

Reservation reservationForInst(const DynamicInst &inst, const ParamDB &db,
                               const std::string &defaultDtype,
                               const ValueStorageLookup &valueStorage) {
  const std::string &form = inst.form.empty() ? defaultDtype : inst.form;
  Reservation out;
  for (const auto &d : inst.dst) {
    if (valueStorage.isRegister(d))
      ++out.preg;
  }
  if (usesShqQueue(db, inst.op, form))
    out.shqQueue = 1;
  if (usesLsq(db, inst.op, form))
    out.lsq = 1;
  if (usesSharedShqCredit(db, inst.op, form))
    out.shq = 1;
  return out;
}

void dumpDispatchLog(const IDU &idu, const std::string &path) {
  std::ofstream os(path);
  for (const auto &r : idu.dispatchLog()) {
    os << "{\"cy\":" << r.cycle
       << ",\"inst_id\":" << r.instId
       << ",\"op\":\"" << jsonEscape(r.op) << "\""
       << ",\"dst\":" << joinJsonArray(r.dst)
       << ",\"src\":" << joinJsonArray(r.src)
       << ",\"top_block_id\":" << r.topBlockId
       << ",\"vreg\":" << r.vreg
       << ",\"SHQ_QUEUE\":" << r.shqQueue
       << ",\"LSQ\":" << r.lsq
       << ",\"SHQ\":" << r.shq
       << "}\n";
  }
}

void dumpVloopTrace(const IDU &idu, const std::string &path) {
  std::ofstream os(path);
  for (const auto &r : idu.vloopTrace()) {
    os << "{\"top_block_id\":" << r.topBlockId
       << ",\"loop_id\":\"" << jsonEscape(r.loopId) << "\""
       << ",\"iter\":" << joinJsonArray(r.iter)
       << ",\"start_cycle\":" << r.startCycle
       << "}\n";
  }
}

} // namespace

SimulationResult runVfInfo(const VfInfo &input,
                           const ParamDB &db,
                           const std::string &resultsDir,
                           int64_t maxCycles) {
  VfInfo vfInfo = input;
  lowerVfInfoValueIds(vfInfo);
  vfInfo.body = normalizeProgramVregLiveRanges(vfInfo.body, vfInfo.params,
                                               vfInfo.values);
  const auto program = canonicalizeSingleSuperIterationLoops(
      vfInfo.body, vfInfo.params, db, vfInfo.defaultDtype);
  ProgramAnalysis analysis(vfInfo.params, vfInfo.values);
  const auto loopBounds = analysis.inferTopBlockLoopBounds(program);
  ProgramFlatten flattener(vfInfo.params);
  const auto &linear = flattener.flatten(program);
  const int topBlocks = static_cast<int>(loopBounds.size());

  IFU ifu(linear, vfInfo.params, &db, loopBounds, topBlocks,
          vfInfo.defaultDtype);
  IDU idu(db.uarch(), db, vfInfo.params, {}, topBlocks, loopBounds,
          vfInfo.defaultDtype, vfInfo.values);
  OoOCoreMainline ooo(db.uarch(), db, vfInfo.defaultDtype, vfInfo.values);
  return runSimulation(ifu, idu, ooo, db.uarch(), vfInfo.params, resultsDir,
                       maxCycles, vfInfo.values);
}

SimulationResult runSimulation(IFU &ifu,
                               IDU &idu,
                               OoOCoreMainline &ooo,
                               const UarchConfig &uarch,
                               const ProgramAnalysis::ParamMap &params,
                               const std::string &resultsDir,
                               int64_t maxCycles,
                               const std::unordered_map<std::string, ValueInfo> &values) {
  if (const char *envMax = std::getenv("PTOAS_VFSIM_MAX_CYCLES")) {
    try {
      const int64_t parsed = std::stoll(envMax);
      if (parsed > 0)
        maxCycles = parsed;
    } catch (...) {
      // Ignore malformed debug override.
    }
  }
  const bool debugCycles = std::getenv("PTOAS_VFSIM_DEBUG_CYCLES") != nullptr;
  const int64_t iduToOooDelay = uarch.iduToOooDelay;
  std::deque<std::pair<int64_t, DynamicInst>> iduToOooPipe;
  const bool useExplicitIduCreditBank = uarch.useExplicitIduCreditBank;
  const ValueStorageLookup valueStorage(values);

  int64_t iduPregCredit = ooo.getFreePreg();
  int64_t iduShqCredit = ooo.getFreeShq();
  int64_t iduPendingShqQueue = 0;
  const std::string dtype = "fp32";
  (void)params;

  int64_t cycle = 0;
  bool completed = false;

  while (cycle < maxCycles) {
    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " begin"
                << " ifu_done=" << (ifu.done() ? 1 : 0)
                << " idu_empty=" << (idu.empty() ? 1 : 0)
                << " rob=" << ooo.getRobSize()
                << " lsq=" << ooo.getLsqSize()
                << " shq=" << ooo.getShqSize() << "\n";
    auto visibleDelta = ooo.updateIduVisibility(cycle);
    if (useExplicitIduCreditBank) {
      iduPregCredit += visibleDelta["preg_free"];
      iduShqCredit += visibleDelta["shq_release"];
    }

    while (!iduToOooPipe.empty() && iduToOooPipe.front().first <= cycle) {
      auto item = std::move(iduToOooPipe.front());
      iduToOooPipe.pop_front();
      if (useExplicitIduCreditBank) {
        // conservative: rebuild reservations from inst metadata
        for (const auto &s : item.second.dst) {
          if (valueStorage.isRegister(s))
            --iduPendingShqQueue;
        }
      }
      ooo.accept(item.second);
    }

    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " fill_idu begin\n";
    int64_t pendingPreg = 0;
    int64_t pendingShqQueue = 0;
    int64_t pendingLsq = 0;
    int64_t pendingShq = 0;
    if (!useExplicitIduCreditBank) {
      for (const auto &item : iduToOooPipe) {
        const auto r = reservationForInst(item.second, idu.db(), dtype, valueStorage);
        pendingPreg += r.preg;
        pendingShqQueue += r.shqQueue;
        pendingLsq += r.lsq;
        pendingShq += r.shq;
      }
    }

    while (idu.canAccept()) {
      if (ifu.done())
        break;
      auto inst = ifu.nextInst();
      if (!inst.has_value())
        break;
      idu.accept(*inst);
    }
    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " fill_idu end\n";

    IDUDispatchBudget budget;
    budget.theoreticalLimitMode = false;
    budget.theoreticalLimitVloopOnly = false;
    budget.freePreg = useExplicitIduCreditBank ? iduPregCredit : std::max<int64_t>(0, ooo.getFreePreg() - pendingPreg);
    budget.freeShqQueue = std::max<int64_t>(0, ooo.getFreeShqQueue() - pendingShqQueue);
    budget.freeLsq = std::max<int64_t>(0, ooo.getFreeLsq() - pendingLsq);
    budget.freeShq = useExplicitIduCreditBank ? iduShqCredit : std::max<int64_t>(0, ooo.getFreeShq() - pendingShq);
    budget.issueBudget = 5;

    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " dispatch begin"
                << " freePreg=" << budget.freePreg
                << " freeShqQueue=" << budget.freeShqQueue
                << " freeLsq=" << budget.freeLsq
                << " freeShq=" << budget.freeShq << "\n";
    auto dispatched = idu.dispatch(cycle, budget);
    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " dispatch end n=" << dispatched.size() << "\n";
    for (const auto &inst : dispatched) {
      if (iduToOooDelay > 0) {
        iduToOooPipe.emplace_back(cycle + iduToOooDelay, inst);
      } else {
        ooo.accept(inst);
      }
    }

    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " ooo begin\n";
    ooo.step();
    if (debugCycles)
      std::cerr << "[vfsim] cycle " << cycle << " ooo end\n";

    if (ifu.done() && idu.empty() &&
        ooo.getRobSize() == 0 && ooo.getLsqSize() == 0 && ooo.getShqSize() == 0 &&
        iduToOooPipe.empty()) {
      completed = true;
      break;
    }

    ++cycle;
  }

  if (!completed)
  {
    if (!resultsDir.empty()) {
      std::filesystem::create_directories(resultsDir);
      ooo.dumpHistory(resultsDir + "/sim_history.json");
      ooo.dumpSimpleLogs(resultsDir + "/start_by_cycle.json", resultsDir + "/done_by_cycle.json");
      dumpDispatchLog(idu, resultsDir + "/idu_to_ooo.json");
      dumpVloopTrace(idu, resultsDir + "/vloop_trace.json");
    }
    throw std::runtime_error(
        "Simulation did not complete before maxCycles"
        " (ifu_done=" + std::string(ifu.done() ? "true" : "false") +
        ", idu_empty=" + std::string(idu.empty() ? "true" : "false") +
        ", rob=" + std::to_string(ooo.getRobSize()) +
        ", lsq=" + std::to_string(ooo.getLsqSize()) +
        ", shq=" + std::to_string(ooo.getShqSize()) +
        ", free_preg=" + std::to_string(ooo.getFreePreg()) +
        ", free_shq=" + std::to_string(ooo.getFreeShq()) +
        ", free_lsq=" + std::to_string(ooo.getFreeLsq()) +
        ", free_shqq=" + std::to_string(ooo.getFreeShqQueue()) + ")");
  }

  if (!resultsDir.empty()) {
    std::filesystem::create_directories(resultsDir);
    ooo.dumpHistory(resultsDir + "/sim_history.json");
    ooo.dumpSimpleLogs(resultsDir + "/start_by_cycle.json", resultsDir + "/done_by_cycle.json");
    dumpDispatchLog(idu, resultsDir + "/idu_to_ooo.json");
    dumpVloopTrace(idu, resultsDir + "/vloop_trace.json");
  }
  return SimulationResult{cycle, ooo.vfEndCycle(), resultsDir};
}

} // namespace vfsim
