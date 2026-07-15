// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IRPlanner.h"
#include "native/IDU.h"
#include "native/IFU.h"
#include "native/OOO.h"
#include "native/ParamDB.h"
#include "native/ProgramFlatten.h"
#include "native/SimulatorRunner.h"
#include "native/TileOpTemplates.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Operation.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/Support/raw_ostream.h"

#include <limits>
#include <optional>
#include <filesystem>

namespace {

constexpr llvm::StringLiteral kFusionGroupIdAttr = "pto.fusion.group_id";
constexpr llvm::StringLiteral kFusionOrderAttr = "pto.fusion.order";
constexpr llvm::StringLiteral kFusionUnrollAttr = "pto.fusion.unroll";

static int64_t getI64Attr(mlir::Operation *op, llvm::StringRef name,
                          int64_t fallback = 0) {
  if (auto attr = op->getAttrOfType<mlir::IntegerAttr>(name))
    return attr.getInt();
  return fallback;
}

static void dumpPlannerGroups(
    const llvm::DenseMap<int64_t, llvm::SmallVector<vfsim::PlannedTileOpIR, 8>> &groups,
    unsigned selectedUnroll) {
  llvm::errs() << "VfSim IR planner: " << groups.size()
               << " fusion group(s), selected unroll=" << selectedUnroll
               << "\n";
  for (const auto &entry : groups) {
    llvm::errs() << "  group " << entry.first << ":";
    llvm::SmallVector<vfsim::PlannedTileOpIR, 8> ordered(entry.second.begin(),
                                                         entry.second.end());
    llvm::sort(ordered, [](const vfsim::PlannedTileOpIR &lhs,
                           const vfsim::PlannedTileOpIR &rhs) {
      return lhs.order < rhs.order;
    });
    for (const vfsim::PlannedTileOpIR &tileOp : ordered) {
      llvm::errs() << " " << tileOp.op->getName().getStringRef() << "#"
                   << tileOp.order;
    }
    llvm::errs() << "\n";
  }
}

static int countTopLevelLoops(const std::vector<vfsim::ProgramNode> &program) {
  int count = 0;
  for (const auto &node : program)
    if (node.kind == vfsim::ProgramNode::Kind::Loop)
      ++count;
  return count;
}

static std::vector<unsigned> enumerateUnrollCandidates(int64_t tripCount,
                                                       unsigned maxUnroll) {
  std::vector<unsigned> candidates;
  const unsigned limit =
      std::max<unsigned>(1, std::min<unsigned>(maxUnroll == 0 ? 1 : maxUnroll,
                                              static_cast<unsigned>(tripCount)));
  for (unsigned value = 1; value <= limit; ++value)
    if (tripCount % value == 0)
      candidates.push_back(value);
  return candidates;
}

static std::optional<int64_t>
simulateCandidate(const vfsim::LoweredTileGroupProgram &lowered,
                  const vfsim::ParamDB &db, unsigned unroll) {
  try {
    vfsim::ProgramAnalysis::ParamMap params;
    params.emplace("vfsim_inner_unroll", static_cast<int64_t>(unroll));

    vfsim::ProgramAnalysis analysis(params);
    const auto loopBounds = analysis.inferTopBlockLoopBounds(lowered.program);
    vfsim::ProgramFlatten flattener(params);
    const auto &linear = flattener.flatten(lowered.program);

    const int topBlocks = countTopLevelLoops(lowered.program);
    vfsim::IFU ifu(linear, {}, &db, loopBounds, topBlocks, lowered.dtype);
    vfsim::IDU idu(db.uarch(), db, {}, {}, topBlocks, loopBounds,
                   lowered.dtype);
    vfsim::OoOCoreMainline ooo(db.uarch(), db, lowered.dtype);

    const auto result = vfsim::runSimulation(
        ifu, idu, ooo, db.uarch(), {}, "", /*maxCycles=*/1000000);
    return result.vfEndCycle;
  } catch (...) {
    return std::nullopt;
  }
}

static std::optional<unsigned>
chooseBestUnroll(const vfsim::LoweredTileGroupProgram &lowered,
                 const vfsim::ParamDB &db, unsigned maxUnroll,
                 bool dumpCandidates) {
  if (lowered.unrollTripCount <= 0)
    return std::nullopt;

  std::optional<unsigned> bestUnroll;
  int64_t bestCycles = std::numeric_limits<int64_t>::max();
  for (unsigned unroll :
       enumerateUnrollCandidates(lowered.unrollTripCount, maxUnroll)) {
    std::optional<int64_t> cycles = simulateCandidate(lowered, db, unroll);
    if (dumpCandidates) {
      llvm::errs() << "  unroll=" << unroll
                   << " trip=" << lowered.unrollTripCount
                   << " dtype=" << lowered.dtype << " cycles=";
      if (cycles)
        llvm::errs() << *cycles;
      else
        llvm::errs() << "failed";
      llvm::errs() << "\n";
    }
    if (!cycles)
      continue;
    if (*cycles < bestCycles) {
      bestCycles = *cycles;
      bestUnroll = unroll;
    }
  }
  return bestUnroll;
}

} // namespace

namespace vfsim {

mlir::LogicalResult planTileFusionIR(mlir::Operation *candidateIR,
                                     const PlannerOptions &options) {
  if (candidateIR == nullptr)
    return mlir::failure();

  llvm::DenseMap<int64_t, llvm::SmallVector<PlannedTileOpIR, 8>> groups;
  candidateIR->walk([&](mlir::Operation *op) {
    auto groupIdAttr = op->getAttrOfType<mlir::IntegerAttr>(kFusionGroupIdAttr);
    if (!groupIdAttr)
      return;
    PlannedTileOpIR tileOp;
    tileOp.op = op;
    tileOp.order = getI64Attr(op, kFusionOrderAttr);
    groups[groupIdAttr.getInt()].push_back(tileOp);
  });

  if (groups.empty())
    return mlir::success();

  mlir::MLIRContext *ctx = candidateIR->getContext();
  std::optional<ParamDB> db;
  try {
    db.emplace(std::filesystem::path(VFSIM_SOURCE_ROOT));
  } catch (const std::exception &ex) {
    if (options.dumpCandidates)
      llvm::errs() << "VfSim IR planner: failed to load params: "
                   << ex.what() << "\n";
    return mlir::success();
  }

  for (auto &entry : groups) {
    if (entry.second.size() < 2)
      continue;
    llvm::SmallVector<PlannedTileOpIR, 8> ordered(entry.second.begin(),
                                                  entry.second.end());
    llvm::sort(ordered, [](const PlannedTileOpIR &lhs,
                           const PlannedTileOpIR &rhs) {
      return lhs.order < rhs.order;
    });

    LoweredTileGroupProgram lowered =
        lowerTileGroupWithPerformanceTemplates(ordered);
    if (!lowered.supported())
      continue;

    std::optional<unsigned> selectedUnroll =
        chooseBestUnroll(lowered, *db, options.maxUnroll,
                         options.dumpCandidates);
    if (!selectedUnroll)
      continue;
    auto unrollAttr = mlir::IntegerAttr::get(mlir::IntegerType::get(ctx, 64),
                                             *selectedUnroll);

    for (const PlannedTileOpIR &tileOp : ordered)
      tileOp.op->setAttr(kFusionUnrollAttr, unrollAttr);
  }

  if (options.dumpCandidates)
    dumpPlannerGroups(groups, options.maxUnroll);

  return mlir::success();
}

} // namespace vfsim
