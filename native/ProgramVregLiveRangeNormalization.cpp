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

struct FlatNormalizeResult {
  std::vector<ProgramNode> nodes;
  std::unordered_map<std::string, std::string> exitAliases;
};

struct ApplyAliasesResult {
  ProgramNode node;
  std::unordered_set<std::string> killedAliases;
};

struct NormalizeNodesResult {
  std::vector<ProgramNode> nodes;
  std::unordered_map<std::string, std::string> exitAliases;
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

void ensureRegisterValue(
    std::unordered_map<std::string, ValueInfo> *values,
    const std::string &slot,
    const std::string &sourceValueId) {
  if (values == nullptr || values->find(slot) != values->end())
    return;

  ValueInfo value;
  auto sourceIt = values->find(sourceValueId);
  if (sourceIt != values->end())
    value = sourceIt->second;
  value.valueId = slot;
  value.storage = ValueStorageKind::Register;
  values->emplace(slot, std::move(value));
}

FlatNormalizeResult normalizeFlatLoopVregs(
    const std::vector<ProgramNode> &body,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats,
    std::unordered_map<std::string, ValueInfo> *values,
    bool hasBackEdge) {
  std::vector<ProgramNode> out = body;
  std::unordered_map<std::string, std::string> firstAccess;
  std::unordered_set<std::string> written;
  for (const ProgramNode &node : out) {
    for (const std::string &src : node.inst.src) {
      if (analysis.isVregName(src) && firstAccess.find(src) == firstAccess.end())
        firstAccess.emplace(src, "read");
    }
    for (const std::string &dst : node.inst.dst) {
      if (!analysis.isVregName(dst))
        continue;
      if (firstAccess.find(dst) == firstAccess.end())
        firstAccess.emplace(dst, "write");
      written.insert(dst);
    }
  }
  std::unordered_set<std::string> loopCarriedVregs;
  if (hasBackEdge) {
    for (const auto &[name, access] : firstAccess) {
      if (access == "read" && written.count(name))
        loopCarriedVregs.insert(name);
    }
  }
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
  std::vector<std::string> slotPool(loopCarriedVregs.begin(),
                                    loopCarriedVregs.end());
  std::sort(slotPool.begin(), slotPool.end(),
            [](const std::string &lhs, const std::string &rhs) {
              return vregSortKey(lhs) < vregSortKey(rhs);
            });

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
          if (loopCarriedVregs.count(slot))
            continue;
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
          if (pos < newSrcs.size() && loopCarriedVregs.count(newSrcs[pos]))
            continue;
          if (pos < newSrcs.size() &&
              !containsSlot(candidateSlots, newSrcs[pos]))
            candidateSlots.push_back(newSrcs[pos]);
        }

        std::string chosenSlot;
        if (loopCarriedVregs.count(dstName)) {
          chosenSlot = dstName;
        } else if (newSrcs.size() == 1 && containsSlot(candidateSlots, newSrcs[0])) {
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
        ensureRegisterValue(values, chosenSlot, dstName);
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

  std::unordered_map<std::string, std::string> exitAliases;
  for (const auto &[logical, slot] : currentSlotByVreg) {
    if (logical != slot)
      exitAliases.emplace(logical, slot);
  }
  return FlatNormalizeResult{std::move(out), std::move(exitAliases)};
}

void collectVregAccesses(const std::vector<ProgramNode> &nodes,
                         const ProgramAnalysis &analysis,
                         std::unordered_map<std::string, std::string> &firstAccess,
                         std::unordered_set<std::string> &written) {
  for (const ProgramNode &node : nodes) {
    if (node.kind == ProgramNode::Kind::Inst) {
      for (const std::string &src : node.inst.src) {
        if (analysis.isVregName(src) && firstAccess.find(src) == firstAccess.end())
          firstAccess.emplace(src, "read");
      }
      for (const std::string &dst : node.inst.dst) {
        if (!analysis.isVregName(dst))
          continue;
        if (firstAccess.find(dst) == firstAccess.end())
          firstAccess.emplace(dst, "write");
        written.insert(dst);
      }
      continue;
    }
    if (node.kind == ProgramNode::Kind::Loop && node.loop) {
      if (analysis.resolveBound(node.loop->iters) <= 0)
        continue;
      collectVregAccesses(node.loop->body, analysis, firstAccess, written);
    }
  }
}

ProgramNode renameVregs(
    const ProgramNode &node,
    const std::unordered_map<std::string, std::string> &aliases,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats) {
  if (node.kind == ProgramNode::Kind::Inst) {
    ProgramInstNode rewritten = node.inst;
    const ProgramInstNode before = rewritten;
    for (std::string &src : rewritten.src) {
      if (!analysis.isVregName(src))
        continue;
      auto it = aliases.find(src);
      if (it != aliases.end())
        src = it->second;
    }
    for (std::string &dst : rewritten.dst) {
      if (!analysis.isVregName(dst))
        continue;
      auto it = aliases.find(dst);
      if (it != aliases.end())
        dst = it->second;
    }
    countFieldChanges(before, rewritten, stats);
    return ProgramNode::makeInst(std::move(rewritten));
  }
  if (node.kind != ProgramNode::Kind::Loop || !node.loop)
    return node;
  ProgramLoopNode rewritten = *node.loop;
  std::vector<ProgramNode> body;
  body.reserve(rewritten.body.size());
  for (const ProgramNode &child : rewritten.body)
    body.push_back(renameVregs(child, aliases, analysis, stats));
  rewritten.body = std::move(body);
  return ProgramNode::makeLoop(std::move(rewritten));
}

std::pair<std::vector<ProgramNode>, std::unordered_set<std::string>>
applyAliasesToNodes(
    const std::vector<ProgramNode> &nodes,
    const std::unordered_map<std::string, std::string> &aliases,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats);

ApplyAliasesResult applyAliasesToNode(
    const ProgramNode &node,
    const std::unordered_map<std::string, std::string> &aliases,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats) {
  if (node.kind == ProgramNode::Kind::Inst) {
    ProgramInstNode rewritten = node.inst;
    const ProgramInstNode before = rewritten;
    for (std::string &src : rewritten.src) {
      if (!analysis.isVregName(src))
        continue;
      auto it = aliases.find(src);
      if (it != aliases.end())
        src = it->second;
    }
    std::unordered_set<std::string> killed;
    for (const std::string &dst : rewritten.dst) {
      if (analysis.isVregName(dst))
        killed.insert(dst);
    }
    countFieldChanges(before, rewritten, stats);
    return ApplyAliasesResult{ProgramNode::makeInst(std::move(rewritten)),
                              std::move(killed)};
  }
  if (node.kind != ProgramNode::Kind::Loop || !node.loop)
    return ApplyAliasesResult{node, {}};

  ProgramLoopNode rewritten = *node.loop;
  if (analysis.resolveBound(rewritten.iters) <= 0)
    return ApplyAliasesResult{ProgramNode::makeLoop(std::move(rewritten)), {}};

  std::unordered_map<std::string, std::string> firstAccess;
  std::unordered_set<std::string> written;
  collectVregAccesses(rewritten.body, analysis, firstAccess, written);
  std::unordered_map<std::string, std::string> carriedAliases;
  for (const auto &[logical, target] : aliases) {
    auto first = firstAccess.find(logical);
    if (first != firstAccess.end() && first->second == "read" && written.count(logical))
      carriedAliases.emplace(logical, target);
  }
  if (!carriedAliases.empty()) {
    std::vector<ProgramNode> renamed;
    renamed.reserve(rewritten.body.size());
    for (const ProgramNode &child : rewritten.body)
      renamed.push_back(renameVregs(child, carriedAliases, analysis, stats));
    rewritten.body = std::move(renamed);
  }

  std::unordered_map<std::string, std::string> remainingAliases = aliases;
  for (const auto &[logical, _] : carriedAliases)
    remainingAliases.erase(logical);
  auto [body, ignoredKills] =
      applyAliasesToNodes(rewritten.body, remainingAliases, analysis, stats);
  (void)ignoredKills;
  rewritten.body = std::move(body);
  for (const auto &[logical, _] : carriedAliases)
    written.erase(logical);
  return ApplyAliasesResult{ProgramNode::makeLoop(std::move(rewritten)),
                            std::move(written)};
}

std::pair<std::vector<ProgramNode>, std::unordered_set<std::string>>
applyAliasesToNodes(
    const std::vector<ProgramNode> &nodes,
    const std::unordered_map<std::string, std::string> &aliases,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats) {
  std::vector<ProgramNode> out;
  out.reserve(nodes.size());
  std::unordered_map<std::string, std::string> activeAliases = aliases;
  std::unordered_set<std::string> killed;
  for (const ProgramNode &node : nodes) {
    ApplyAliasesResult result =
        applyAliasesToNode(node, activeAliases, analysis, stats);
    out.push_back(std::move(result.node));
    for (const std::string &name : result.killedAliases) {
      activeAliases.erase(name);
      killed.insert(name);
    }
  }
  return {std::move(out), std::move(killed)};
}

NormalizeNodesResult normalizeNodes(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis &analysis,
    ProgramVregLiveRangeNormalizationStats &stats,
    std::unordered_map<std::string, ValueInfo> *values) {
  std::vector<ProgramNode> out;
  out.reserve(program.size());
  std::unordered_map<std::string, std::string> activeAliases;
  for (const ProgramNode &node : program) {
    ApplyAliasesResult applied =
        applyAliasesToNode(node, activeAliases, analysis, stats);
    if (applied.node.kind != ProgramNode::Kind::Loop || !applied.node.loop) {
      out.push_back(std::move(applied.node));
      for (const std::string &name : applied.killedAliases)
        activeAliases.erase(name);
      continue;
    }

    const ProgramNode &loopNode = applied.node;
    const bool flatInstBody =
        std::all_of(loopNode.loop->body.begin(), loopNode.loop->body.end(),
                    [](const ProgramNode &op) {
                      return op.kind == ProgramNode::Kind::Inst;
                    });
    ProgramLoopNode rewritten = *loopNode.loop;
    const int64_t iters = analysis.resolveBound(rewritten.iters);
    if (iters <= 0) {
      out.push_back(ProgramNode::makeLoop(std::move(rewritten)));
      continue;
    }
    std::unordered_map<std::string, std::string> childAliases;
    if (flatInstBody) {
      FlatNormalizeResult result = normalizeFlatLoopVregs(
          rewritten.body, analysis, stats, values, iters > 1);
      rewritten.body = std::move(result.nodes);
      childAliases = std::move(result.exitAliases);
    } else {
      NormalizeNodesResult result =
          normalizeNodes(rewritten.body, analysis, stats, values);
      rewritten.body = std::move(result.nodes);
      childAliases = std::move(result.exitAliases);
    }
    out.push_back(ProgramNode::makeLoop(std::move(rewritten)));
    for (const std::string &name : applied.killedAliases)
      activeAliases.erase(name);
    for (const auto &[logical, slot] : childAliases)
      activeAliases[logical] = slot;
  }
  return NormalizeNodesResult{std::move(out), std::move(activeAliases)};
}

} // namespace

std::vector<ProgramNode> normalizeProgramVregLiveRanges(
    const std::vector<ProgramNode> &program,
    const ProgramAnalysis::ParamMap &params,
    const std::unordered_map<std::string, ValueInfo> &values,
    ProgramVregLiveRangeNormalizationStats *stats) {
  ProgramVregLiveRangeNormalizationStats localStats;
  ProgramAnalysis analysis(params, values);
  auto normalized = normalizeNodes(program, analysis, localStats, nullptr).nodes;
  if (stats != nullptr)
    *stats = localStats;
  return normalized;
}

void normalizeProgramVregLiveRanges(
    VfInfo &vfInfo,
    ProgramVregLiveRangeNormalizationStats *stats) {
  ProgramVregLiveRangeNormalizationStats localStats;
  ProgramAnalysis analysis(vfInfo.params, vfInfo.values);
  vfInfo.body = normalizeNodes(vfInfo.body, analysis, localStats,
                               &vfInfo.values).nodes;
  if (stats != nullptr)
    *stats = localStats;
}

} // namespace vfsim
