// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER
// EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
// MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE in the root
// of the software repository for the full text of the License.

#include "native/IRPlanner.h"
#include "native/ParamDB.h"
#include "native/SimulatorRunner.h"
#include "native/TileOpTemplates.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Operation.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Support/raw_ostream.h"

#include <filesystem>
#include <limits>
#include <optional>
#include <string>
#include <vector>

namespace {

constexpr llvm::StringLiteral kFusionGroupIdAttr = "pto.fusion.group_id";
constexpr llvm::StringLiteral kFusionOrderAttr = "pto.fusion.order";
constexpr llvm::StringLiteral kFusionRowUnrollAttr =
    "pto.fusion.row_unroll_factor";
constexpr llvm::StringLiteral kFusionColUnrollAttr =
    "pto.fusion.col_unroll_factor";

int64_t getI64Attr(mlir::Operation *op, llvm::StringRef name,
                   int64_t fallback = 0) {
  if (auto attr = op->getAttrOfType<mlir::IntegerAttr>(name))
    return attr.getInt();
  return fallback;
}

std::vector<unsigned> enumerateUnrollCandidates(int64_t tripCount,
                                                unsigned maxUnroll) {
  std::vector<unsigned> candidates;
  const unsigned limit = std::max<unsigned>(
      1, std::min<unsigned>(maxUnroll == 0 ? 1 : maxUnroll,
                            static_cast<unsigned>(tripCount)));
  for (unsigned factor = 1; factor <= limit; ++factor) {
    if (tripCount % factor == 0)
      candidates.push_back(factor);
  }
  return candidates;
}

enum class CandidateMode {
  NoUnroll,
  Abcabc,
  Aabbcc,
};

llvm::StringRef candidateModeName(CandidateMode mode) {
  switch (mode) {
  case CandidateMode::NoUnroll:
    return "NO_UNROLL";
  case CandidateMode::Abcabc:
    return "ABCABC";
  case CandidateMode::Aabbcc:
    return "AABBCC";
  }
  return "UNKNOWN";
}

llvm::StringRef traversalName(vfsim::TileTraversal traversal) {
  return traversal == vfsim::TileTraversal::OneDimensional ? "1D" : "2D";
}

std::optional<int64_t>
simulateCandidate(const vfsim::LoweredTileGroupProgram &lowered,
                  const vfsim::ParamDB &db, unsigned factor, CandidateMode mode,
                  std::string *failureReason) {
  try {
    const vfsim::TileUnrollOrder order = mode == CandidateMode::Aabbcc
                                             ? vfsim::TileUnrollOrder::Aabbcc
                                             : vfsim::TileUnrollOrder::Abcabc;
    const vfsim::CanonicalVfInfo candidate =
        vfsim::materializeTileUnrollCandidate(lowered, factor, order);
    const auto result =
        vfsim::runCanonicalVfInfo(candidate, db, "", /*maxCycles=*/1000000);
    return result.vfEndCycle;
  } catch (const std::exception &exception) {
    if (failureReason)
      *failureReason = exception.what();
  } catch (...) {
    if (failureReason)
      *failureReason = "unknown exception";
  }
  return std::nullopt;
}

struct SelectedUnroll {
  unsigned factor = 1;
  CandidateMode mode = CandidateMode::NoUnroll;
  int64_t cycles = 0;
};

std::optional<SelectedUnroll>
chooseBestUnroll(const vfsim::LoweredTileGroupProgram &lowered,
                 const vfsim::ParamDB &db, unsigned maxUnroll,
                 bool dumpCandidates, int64_t groupId,
                 std::string *failureReason) {
  if (lowered.unrollTripCount <= 0 || lowered.modeledLoopTripCount <= 0)
    return std::nullopt;

  if (dumpCandidates) {
    llvm::errs() << "VfSim group=" << groupId
                 << " template=" << lowered.selectedTemplate
                 << " traversal=" << traversalName(lowered.traversal)
                 << " modeled_trip=" << lowered.modeledLoopTripCount
                 << " candidate_trip=" << lowered.unrollTripCount
                 << " dtype=" << lowered.dtype << "\n";
  }

  std::string baselineFailure;
  const std::optional<int64_t> baseline = simulateCandidate(
      lowered, db, /*factor=*/1, CandidateMode::NoUnroll, &baselineFailure);
  if (dumpCandidates) {
    llvm::errs() << "  mode=NO_UNROLL unroll=1 trip=" << lowered.unrollTripCount
                 << " dtype=" << lowered.dtype << " cycles=";
    if (baseline)
      llvm::errs() << *baseline;
    else
      llvm::errs() << "failed";
    llvm::errs() << "\n";
  }
  if (!baseline) {
    if (failureReason)
      *failureReason = baselineFailure.empty() ? "baseline simulation failed"
                                               : baselineFailure;
    return std::nullopt;
  }

  SelectedUnroll best{/*factor=*/1, CandidateMode::NoUnroll, *baseline};
  for (unsigned factor :
       enumerateUnrollCandidates(lowered.unrollTripCount, maxUnroll)) {
    if (factor == 1)
      continue;
    for (CandidateMode mode : {CandidateMode::Abcabc, CandidateMode::Aabbcc}) {
      std::string candidateFailure;
      const std::optional<int64_t> cycles =
          simulateCandidate(lowered, db, factor, mode, &candidateFailure);
      if (dumpCandidates) {
        llvm::errs() << "  mode=" << candidateModeName(mode)
                     << " unroll=" << factor
                     << " trip=" << lowered.unrollTripCount
                     << " dtype=" << lowered.dtype << " cycles=";
        if (cycles)
          llvm::errs() << *cycles;
        else
          llvm::errs() << "failed";
        llvm::errs() << "\n";
      }
      if (!cycles) {
        llvm::errs() << "warning: VfSim planner skipped "
                     << candidateModeName(mode) << " unroll=" << factor
                     << " for fusion group " << groupId;
        if (!candidateFailure.empty())
          llvm::errs() << ": " << candidateFailure;
        llvm::errs() << "\n";
        continue;
      }
      if (*cycles < best.cycles)
        best = SelectedUnroll{factor, mode, *cycles};
    }
  }

  if (dumpCandidates) {
    llvm::errs() << "  selected mode=" << candidateModeName(best.mode)
                 << " unroll=" << best.factor << " cycles=" << best.cycles
                 << "\n";
  }
  return best;
}

void dumpPlannerGroups(
    const llvm::DenseMap<int64_t, llvm::SmallVector<vfsim::PlannedTileOpIR, 8>>
        &groups) {
  llvm::errs() << "VfSim IR planner: " << groups.size() << " fusion group(s)\n";
  for (const auto &entry : groups) {
    const int64_t rowUnroll =
        entry.second.empty()
            ? -1
            : getI64Attr(entry.second.front().op, kFusionRowUnrollAttr, -1);
    const int64_t colUnroll =
        entry.second.empty()
            ? -1
            : getI64Attr(entry.second.front().op, kFusionColUnrollAttr, -1);
    llvm::errs() << "  group " << entry.first << " row_unroll=" << rowUnroll
                 << " col_unroll=" << colUnroll << ":";
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

} // namespace

namespace vfsim {

mlir::LogicalResult planTileFusionIR(mlir::Operation *candidateIR,
                                     const PlannerOptions &options) {
  if (!candidateIR)
    return mlir::failure();

  llvm::DenseMap<int64_t, llvm::SmallVector<PlannedTileOpIR, 8>> groups;
  candidateIR->walk([&](mlir::Operation *op) {
    auto groupId = op->getAttrOfType<mlir::IntegerAttr>(kFusionGroupIdAttr);
    if (!groupId)
      return;
    groups[groupId.getInt()].push_back(
        PlannedTileOpIR{op, getI64Attr(op, kFusionOrderAttr)});
  });
  if (groups.empty())
    return mlir::success();

  std::optional<ParamDB> db;
  try {
    db.emplace(std::filesystem::path(VFSIM_SOURCE_ROOT));
  } catch (const std::exception &exception) {
    llvm::errs() << "warning: VfSim planner disabled: failed to load "
                    "parameter database: "
                 << exception.what() << "\n";
    return mlir::success();
  }

  mlir::MLIRContext *context = candidateIR->getContext();
  for (auto &entry : groups) {
    if (entry.second.size() < 2)
      continue;
    llvm::SmallVector<PlannedTileOpIR, 8> ordered(entry.second.begin(),
                                                  entry.second.end());
    llvm::sort(ordered,
               [](const PlannedTileOpIR &lhs, const PlannedTileOpIR &rhs) {
                 return lhs.order < rhs.order;
               });

    LoweredTileGroupProgram lowered = lowerTileGroupToCanonicalVfInfo(ordered);
    if (!lowered.supported()) {
      llvm::errs() << "warning: VfSim planner skipped fusion group "
                   << entry.first << ": " << lowered.unsupportedReason << "\n";
      continue;
    }

    std::string selectionFailure;
    const std::optional<SelectedUnroll> selected = chooseBestUnroll(
        lowered, *db, options.maxUnroll, options.dumpCandidates, entry.first,
        &selectionFailure);
    if (!selected) {
      llvm::errs() << "warning: VfSim planner skipped fusion group "
                   << entry.first << ": failed to select unroll factor";
      if (!selectionFailure.empty())
        llvm::errs() << ": " << selectionFailure;
      llvm::errs() << "\n";
      continue;
    }

    int64_t rowUnroll = 1;
    int64_t colUnroll = 1;
    switch (lowered.unrollDimension) {
    case UnrollLoopDimension::Row:
      rowUnroll = selected->factor;
      break;
    case UnrollLoopDimension::Col:
      colUnroll = selected->factor;
      break;
    case UnrollLoopDimension::None:
      continue;
    }
    const auto rowAttr =
        mlir::IntegerAttr::get(mlir::IntegerType::get(context, 64), rowUnroll);
    const auto colAttr =
        mlir::IntegerAttr::get(mlir::IntegerType::get(context, 64), colUnroll);
    for (const PlannedTileOpIR &tileOp : ordered) {
      tileOp.op->setAttr(kFusionRowUnrollAttr, rowAttr);
      tileOp.op->setAttr(kFusionColUnrollAttr, colAttr);
    }
  }

  if (options.dumpCandidates)
    dumpPlannerGroups(groups);
  return mlir::success();
}

} // namespace vfsim
