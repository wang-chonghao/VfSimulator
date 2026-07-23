// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramVregLiveRangeNormalization.h"

#include <algorithm>
#include <cctype>
#include <limits>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace vfsim {
namespace {

struct VregVersion {
  std::string name;
  int64_t generation = 0;
};

std::string makeVersionKey(const VregVersion &version) {
  return version.name + "#" + std::to_string(version.generation);
}

std::pair<int64_t, std::string> vregSortKey(const std::string &name) {
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

bool containsSlot(const std::vector<std::string> &slots,
                  const std::string &slot) {
  return std::find(slots.begin(), slots.end(), slot) != slots.end();
}

std::string nextFreshVreg(const std::vector<std::string> &slotPool) {
  std::unordered_set<std::string> used(slotPool.begin(), slotPool.end());
  int64_t maxIndex = -1;
  for (const std::string &name : slotPool) {
    auto key = vregSortKey(name);
    if (key.first != std::numeric_limits<int64_t>::max())
      maxIndex = std::max(maxIndex, key.first);
  }

  for (int64_t candidate = maxIndex + 1;; ++candidate) {
    std::string name = "v" + std::to_string(candidate);
    if (used.find(name) == used.end())
      return name;
  }
}

void countFieldChanges(const ProgramInstNode &before,
                       const ProgramInstNode &after,
                       ProgramVregLiveRangeNormalizationStats &stats) {
  const size_t srcCount = std::min(before.src.size(), after.src.size());
  for (size_t i = 0; i < srcCount; ++i) {
    if (before.src[i] != after.src[i])
      ++stats.changedFields;
  }
  const size_t dstCount = std::min(before.dst.size(), after.dst.size());
  for (size_t i = 0; i < dstCount; ++i) {
    if (before.dst[i] != after.dst[i])
      ++stats.changedFields;
  }
}

std::vector<ProgramNode> normalizeFlatLoopVregs(
    const std::vector<ProgramNode> &body,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats) {
  std::vector<ProgramNode> out = body;
  std::unordered_map<std::string, VregVersion> currentVersionByVreg;
  std::unordered_map<std::string, int64_t> versionCounter;
  std::vector<std::vector<std::optional<std::string>>> srcVersions(out.size());
  std::vector<std::vector<std::optional<std::string>>> dstVersions(out.size());
  std::unordered_map<std::string, int64_t> lastUse;

  for (size_t idx = 0; idx < out.size(); ++idx) {
    ProgramInstNode &inst = out[idx].inst;
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

  for (size_t idx = 0; idx < out.size(); ++idx) {
    ProgramInstNode before = out[idx].inst;
    ProgramInstNode &inst = out[idx].inst;
    std::vector<std::string> newSrcs = inst.src;

    for (size_t pos = 0; pos < inst.src.size(); ++pos) {
      const std::string &src = inst.src[pos];
      if (!analysis.isVregName(src))
        continue;

      std::string slot = src;
      const std::optional<std::string> &version =
          pos < srcVersions[idx].size() ? srcVersions[idx][pos]
                                        : std::nullopt;
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
          std::sort(candidateSlots.begin(), candidateSlots.end(),
                    [](const std::string &lhs, const std::string &rhs) {
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
    countFieldChanges(before, inst, stats);
  }

  return out;
}

std::vector<ProgramNode> normalizeNodes(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats) {
  std::vector<ProgramNode> out;
  out.reserve(program.size());
  for (const ProgramNode &node : program) {
    if (node.kind != ProgramNode::Kind::Loop || !node.loop) {
      out.push_back(node);
      continue;
    }

    const bool flatInstBody =
        std::all_of(node.loop->body.begin(), node.loop->body.end(),
                    [](const ProgramNode &op) {
          return op.kind == ProgramNode::Kind::Inst;
        });
    ProgramLoopNode rewritten = *node.loop;
    if (flatInstBody) {
      rewritten.body = normalizeFlatLoopVregs(rewritten.body, analysis, stats);
    } else {
      rewritten.body = normalizeNodes(rewritten.body, analysis, stats);
    }
    out.push_back(ProgramNode::makeLoop(std::move(rewritten)));
  }
  return out;
}

} // namespace

std::vector<ProgramNode> normalizeProgramVregLiveRanges(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis::ParamMap &params,
    const std::unordered_map<std::string, ValueInfo> &values,
    ProgramVregLiveRangeNormalizationStats *stats) {
  ProgramVregLiveRangeNormalizationStats localStats;
  ProgramAnalysis analysis(params, values);
  auto normalized = normalizeNodes(program, analysis, localStats);
  if (stats != nullptr)
    *stats = localStats;
  return normalized;
}

} // namespace vfsim
