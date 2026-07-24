// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IRPlanner.h"
#include "native/ParamDB.h"
#include "native/ProgramAnalysis.h"
#include "native/ProgramCanonicalization.h"
#include "native/SimulatorRunner.h"
#include "native/TileOpTemplates.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Operation.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/raw_ostream.h"

#include <limits>
#include <optional>
#include <filesystem>
#include <cctype>
#include <cstdlib>
#include <unordered_map>
#include <unordered_set>

namespace {

constexpr llvm::StringLiteral kFusionGroupIdAttr = "pto.fusion.group_id";
constexpr llvm::StringLiteral kFusionOrderAttr = "pto.fusion.order";
constexpr llvm::StringLiteral kFusionRowUnrollAttr =
    "pto.fusion.row_unroll_factor";
constexpr llvm::StringLiteral kFusionColUnrollAttr =
    "pto.fusion.col_unroll_factor";

static int64_t getI64Attr(mlir::Operation *op, llvm::StringRef name,
                          int64_t fallback = 0) {
  if (auto attr = op->getAttrOfType<mlir::IntegerAttr>(name))
    return attr.getInt();
  return fallback;
}

static void dumpPlannerGroups(
    const llvm::DenseMap<int64_t, llvm::SmallVector<vfsim::PlannedTileOpIR, 8>> &groups) {
  llvm::errs() << "VfSim IR planner: " << groups.size()
               << " fusion group(s)\n";
  for (const auto &entry : groups) {
    int64_t rowUnroll =
        entry.second.empty()
            ? -1
            : getI64Attr(entry.second.front().op, kFusionRowUnrollAttr, -1);
    int64_t colUnroll =
        entry.second.empty()
            ? -1
            : getI64Attr(entry.second.front().op, kFusionColUnrollAttr, -1);
    llvm::errs() << "  group " << entry.first
                 << " row_unroll=" << rowUnroll
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

static std::optional<std::filesystem::path> getDumpDir() {
  const char *raw = std::getenv("PTOAS_VFSIM_DUMP_DIR");
  if (raw == nullptr || raw[0] == '\0')
    return std::nullopt;
  std::filesystem::path dir(raw);
  std::error_code ec;
  std::filesystem::create_directories(dir, ec);
  if (ec)
    return std::nullopt;
  return dir;
}

static std::string jsonEscape(llvm::StringRef text) {
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

static void dumpStringArray(llvm::raw_ostream &os,
                            llvm::ArrayRef<std::string> values) {
  os << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      os << ", ";
    os << "\"" << jsonEscape(values[i]) << "\"";
  }
  os << "]";
}

static void dumpProgramNode(llvm::raw_ostream &os,
                            const vfsim::ProgramNode &node,
                            unsigned indent);

static void dumpProgramNodes(llvm::raw_ostream &os,
                             llvm::ArrayRef<vfsim::ProgramNode> nodes,
                             unsigned indent) {
  os << "[\n";
  for (size_t i = 0; i < nodes.size(); ++i) {
    os.indent(indent + 2);
    dumpProgramNode(os, nodes[i], indent + 2);
    if (i + 1 != nodes.size())
      os << ",";
    os << "\n";
  }
  os.indent(indent) << "]";
}

static void dumpProgramNode(llvm::raw_ostream &os,
                            const vfsim::ProgramNode &node,
                            unsigned indent) {
  if (node.kind == vfsim::ProgramNode::Kind::Loop && node.loop) {
    os << "{\"type\":\"loop\",\"name\":\"" << jsonEscape(node.loop->name)
       << "\",\"iters\":\"" << jsonEscape(node.loop->iters)
       << "\",\"unroll\":\"" << jsonEscape(node.loop->unroll)
       << "\",\"body\":";
    dumpProgramNodes(os, node.loop->body, indent);
    os << "}";
    return;
  }

  os << "{\"type\":\"inst\",\"op\":\"" << jsonEscape(node.inst.op)
     << "\",\"form\":\"" << jsonEscape(node.inst.form) << "\",\"src\":";
  dumpStringArray(os, node.inst.src);
  os << ",\"dst\":";
  dumpStringArray(os, node.inst.dst);
  os << "}";
}

static void dumpVfInfo(const vfsim::VfInfo &vfInfo,
                       const std::filesystem::path &path,
                       llvm::StringRef note) {
  std::error_code ec;
  llvm::raw_fd_ostream os(path.string(), ec, llvm::sys::fs::OF_Text);
  if (ec)
    return;

  os << "{\n";
  os << "  \"note\": \"" << jsonEscape(note) << "\",\n";
  os << "  \"dtype\": \"" << jsonEscape(vfInfo.defaultDtype) << "\",\n";
  os << "  \"params\": {";
  bool first = true;
  for (const auto &entry : vfInfo.params) {
    if (!first)
      os << ", ";
    first = false;
    os << "\"" << jsonEscape(entry.first) << "\": " << entry.second;
  }
  os << "},\n";
  os << "  \"program\": ";
  dumpProgramNodes(os, vfInfo.body, 2);
  os << "\n}\n";
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

static void normalizeVregLiveRanges(std::vector<vfsim::ProgramNode> &program);

static std::optional<int64_t>
simulateCandidate(const vfsim::LoweredTileGroupProgram &lowered,
                  const vfsim::ParamDB &db, unsigned unroll,
                  const std::filesystem::path *dumpDir,
                  int64_t groupId) {
  try {
    vfsim::VfInfo vfInfo = lowered.vfInfo;
    vfInfo.params["vfsim_inner_unroll"] = static_cast<int64_t>(unroll);
    normalizeVregLiveRanges(vfInfo.body);

    vfsim::ProgramCanonicalizationStats stats;
    vfsim::VfInfo expanded = vfInfo;
    expanded.body = vfsim::canonicalizeSingleSuperIterationLoops(
        vfInfo.body, vfInfo.params, db, vfInfo.defaultDtype, &stats);
    expanded.params.erase("vfsim_inner_unroll");

    if (dumpDir != nullptr) {
      const std::string stem = "group" + std::to_string(groupId) +
                               "_candidate_unroll" + std::to_string(unroll);
      dumpVfInfo(vfInfo, *dumpDir / (stem + "_before_expand.json"),
                 "VfSim candidate before loop canonicalization");
      dumpVfInfo(expanded, *dumpDir / (stem + "_after_expand.json"),
                 "VfSim candidate after loop canonicalization");
    }

    const auto result =
        vfsim::runVfInfo(expanded, db, "", /*maxCycles=*/1000000);
    return result.vfEndCycle;
  } catch (...) {
    return std::nullopt;
  }
}

struct VregVersion {
  std::string name;
  int64_t generation = 0;
};

static std::string makeVersionKey(const VregVersion &version) {
  return version.name + "#" + std::to_string(version.generation);
}

static std::pair<int64_t, std::string> vregSortKey(const std::string &name) {
  if (name.size() <= 1)
    return {std::numeric_limits<int64_t>::max(), name};
  int64_t value = 0;
  for (size_t i = 1; i < name.size(); ++i) {
    if (!std::isdigit(static_cast<unsigned char>(name[i])))
      return {std::numeric_limits<int64_t>::max(), name};
    value = value * 10 + static_cast<int64_t>(name[i] - '0');
  }
  return {value, name};
}

static bool containsSlot(const std::vector<std::string> &slots,
                         llvm::StringRef slot) {
  return llvm::is_contained(slots, slot);
}

static std::string nextFreshVreg(const std::vector<std::string> &slotPool) {
  std::unordered_set<std::string> used(slotPool.begin(), slotPool.end());
  int64_t maxIndex = -1;
  for (const std::string &name : slotPool) {
    auto key = vregSortKey(name);
    if (key.first != std::numeric_limits<int64_t>::max())
      maxIndex = std::max(maxIndex, key.first);
  }

  for (int64_t candidate = maxIndex + 1;; ++candidate) {
    std::string name = "v" + std::to_string(candidate);
    if (!used.count(name))
      return name;
  }
}

static void normalizeFlatLoopVregs(std::vector<vfsim::ProgramNode> &body) {
  vfsim::ProgramAnalysis analysis;
  std::unordered_map<std::string, VregVersion> currentVersionByVreg;
  std::unordered_map<std::string, int64_t> versionCounter;
  std::vector<std::vector<std::optional<std::string>>> srcVersions(body.size());
  std::vector<std::vector<std::optional<std::string>>> dstVersions(body.size());
  std::unordered_map<std::string, int64_t> lastUse;

  for (size_t idx = 0; idx < body.size(); ++idx) {
    vfsim::ProgramInstNode &inst = body[idx].inst;
    srcVersions[idx].reserve(inst.src.size());
    for (const std::string &src : inst.src) {
      if (!analysis.isVregName(src)) {
        srcVersions[idx].push_back(std::nullopt);
        continue;
      }
      auto it = currentVersionByVreg.find(src);
      if (it == currentVersionByVreg.end()) {
        srcVersions[idx].push_back(std::nullopt);
        continue;
      }
      std::string key = makeVersionKey(it->second);
      srcVersions[idx].push_back(key);
      lastUse[key] = static_cast<int64_t>(idx);
    }

    dstVersions[idx].reserve(inst.dst.size());
    for (const std::string &dst : inst.dst) {
      if (!analysis.isVregName(dst)) {
        dstVersions[idx].push_back(std::nullopt);
        continue;
      }
      int64_t &generation = versionCounter[dst];
      ++generation;
      VregVersion version{dst, generation};
      currentVersionByVreg[dst] = version;
      dstVersions[idx].push_back(makeVersionKey(version));
    }
  }

  std::unordered_map<std::string, std::string> currentSlotByVreg;
  std::unordered_map<std::string, std::string> slotOfVersion;
  std::unordered_map<std::string, std::optional<std::string>> slotOccupant;
  std::vector<std::string> slotPool;

  for (size_t idx = 0; idx < body.size(); ++idx) {
    vfsim::ProgramInstNode &inst = body[idx].inst;
    std::vector<std::string> newSrcs = inst.src;

    for (size_t pos = 0; pos < inst.src.size(); ++pos) {
      const std::string &src = inst.src[pos];
      if (!analysis.isVregName(src))
        continue;

      std::string slot = src;
      const std::optional<std::string> &version =
          pos < srcVersions[idx].size() ? srcVersions[idx][pos] : std::nullopt;
      if (version) {
        auto slotIt = slotOfVersion.find(*version);
        if (slotIt != slotOfVersion.end()) {
          slot = slotIt->second;
        } else {
          size_t hash = version->find('#');
          std::string versionName =
              hash == std::string::npos ? src : version->substr(0, hash);
          auto curIt = currentSlotByVreg.find(versionName);
          slot = curIt == currentSlotByVreg.end() ? versionName
                                                  : curIt->second;
        }
      } else {
        auto curIt = currentSlotByVreg.find(src);
        if (curIt != currentSlotByVreg.end())
          slot = curIt->second;
      }

      newSrcs[pos] = slot;
    }

    std::vector<std::string> newDsts = inst.dst;
    if (inst.dst.size() == 1 && analysis.isVregName(inst.dst.front())) {
      const std::string &dstName = inst.dst.front();
      const std::optional<std::string> &dstVersion =
          dstVersions[idx].empty() ? std::nullopt : dstVersions[idx].front();
      if (dstVersion) {
        std::vector<std::string> candidateSlots;
        for (const std::string &slot : slotPool) {
          auto occIt = slotOccupant.find(slot);
          bool reusable = occIt == slotOccupant.end() || !occIt->second;
          if (!reusable) {
            auto lastIt = lastUse.find(*occIt->second);
            int64_t last = lastIt == lastUse.end() ? -1 : lastIt->second;
            reusable = last < static_cast<int64_t>(idx);
          }
          if (reusable)
            candidateSlots.push_back(slot);
        }

        for (size_t pos = 0; pos < srcVersions[idx].size(); ++pos) {
          const std::optional<std::string> &version = srcVersions[idx][pos];
          if (!version)
            continue;
          auto lastIt = lastUse.find(*version);
          if (lastIt == lastUse.end() ||
              lastIt->second != static_cast<int64_t>(idx))
            continue;
          if (pos < newSrcs.size() &&
              !containsSlot(candidateSlots, newSrcs[pos]))
            candidateSlots.push_back(newSrcs[pos]);
        }

        std::string chosenSlot;
        if (newSrcs.size() == 1 && containsSlot(candidateSlots, newSrcs[0])) {
          chosenSlot = newSrcs[0];
        } else if (!candidateSlots.empty()) {
          llvm::sort(candidateSlots, [](const std::string &lhs,
                                        const std::string &rhs) {
            return vregSortKey(lhs) < vregSortKey(rhs);
          });
          chosenSlot = candidateSlots.front();
        } else if (!containsSlot(slotPool, dstName)) {
          chosenSlot = dstName;
          slotPool.push_back(chosenSlot);
        } else {
          chosenSlot = nextFreshVreg(slotPool);
          slotPool.push_back(chosenSlot);
        }

        if (!containsSlot(slotPool, chosenSlot))
          slotPool.push_back(chosenSlot);
        slotOfVersion[*dstVersion] = chosenSlot;
        currentSlotByVreg[dstName] = chosenSlot;
        slotOccupant[chosenSlot] = *dstVersion;
        newDsts[0] = chosenSlot;
      }
    }

    inst.src = std::move(newSrcs);
    inst.dst = std::move(newDsts);
  }
}

static void normalizeVregLiveRanges(std::vector<vfsim::ProgramNode> &program) {
  for (vfsim::ProgramNode &node : program) {
    if (node.kind != vfsim::ProgramNode::Kind::Loop || !node.loop)
      continue;

    const bool flatInstBody = llvm::all_of(node.loop->body, [](const auto &op) {
      return op.kind == vfsim::ProgramNode::Kind::Inst;
    });
    if (flatInstBody) {
      normalizeFlatLoopVregs(node.loop->body);
      continue;
    }
    normalizeVregLiveRanges(node.loop->body);
  }
}

static std::optional<unsigned>
chooseBestUnroll(const vfsim::LoweredTileGroupProgram &lowered,
                 const vfsim::ParamDB &db, unsigned maxUnroll,
                 bool dumpCandidates,
                 const std::filesystem::path *dumpDir,
                 int64_t groupId) {
  if (lowered.unrollTripCount <= 0)
    return std::nullopt;

  std::optional<unsigned> bestUnroll;
  int64_t bestCycles = std::numeric_limits<int64_t>::max();
  for (unsigned unroll :
       enumerateUnrollCandidates(lowered.unrollTripCount, maxUnroll)) {
    std::optional<int64_t> cycles =
        simulateCandidate(lowered, db, unroll, dumpDir, groupId);
    if (dumpCandidates) {
      llvm::errs() << "  unroll=" << unroll
                   << " trip=" << lowered.unrollTripCount
                   << " dtype=" << lowered.vfInfo.defaultDtype << " cycles=";
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

  std::optional<std::filesystem::path> dumpDir = getDumpDir();
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

    if (dumpDir) {
      const std::string inputName =
          "group" + std::to_string(entry.first) + "_vfsim_input.json";
      dumpVfInfo(lowered.vfInfo, *dumpDir / inputName,
                 "VfSim input lowered from PTOAS fusion group");
    }

    std::optional<unsigned> selectedUnroll =
        chooseBestUnroll(lowered, *db, options.maxUnroll,
                         options.dumpCandidates,
                         dumpDir ? &*dumpDir : nullptr, entry.first);
    if (!selectedUnroll)
      continue;
    int64_t rowUnroll = 1;
    int64_t colUnroll = 1;
    switch (lowered.unrollDimension) {
    case UnrollLoopDimension::Row:
      rowUnroll = *selectedUnroll;
      break;
    case UnrollLoopDimension::Col:
      colUnroll = *selectedUnroll;
      break;
    case UnrollLoopDimension::None:
      continue;
    }
    auto rowUnrollAttr = mlir::IntegerAttr::get(
        mlir::IntegerType::get(ctx, 64), rowUnroll);
    auto colUnrollAttr = mlir::IntegerAttr::get(
        mlir::IntegerType::get(ctx, 64), colUnroll);

    for (const PlannedTileOpIR &tileOp : ordered) {
      tileOp.op->setAttr(kFusionRowUnrollAttr, rowUnrollAttr);
      tileOp.op->setAttr(kFusionColUnrollAttr, colUnrollAttr);
    }
  }

  if (options.dumpCandidates)
    dumpPlannerGroups(groups);

  return mlir::success();
}

} // namespace vfsim
