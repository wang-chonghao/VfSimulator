// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ParamDB.h"

#include "native/Json.h"

#include <algorithm>
#include <cstdlib>
#include <optional>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

using JsonValue = json::Value;

const JsonValue *findKey(const JsonValue::Object &object, const std::string &key) {
  auto it = object.find(key);
  if (it == object.end())
    return nullptr;
  return &it->second;
}

std::string readStringField(const JsonValue::Object &object, const char *key,
                            const std::string &defaultValue = {}) {
  const JsonValue *value = findKey(object, key);
  return value ? value->asString(defaultValue) : defaultValue;
}

int64_t readIntField(const JsonValue::Object &object, const char *key,
                     int64_t defaultValue = 0) {
  const JsonValue *value = findKey(object, key);
  return value ? value->asInt(defaultValue) : defaultValue;
}

bool readBoolField(const JsonValue::Object &object, const char *key,
                   bool defaultValue = false) {
  const JsonValue *value = findKey(object, key);
  return value ? value->asBool(defaultValue) : defaultValue;
}

std::pair<std::string, std::string> splitQualifiedOp(const std::string &name) {
  const std::size_t dot = name.rfind('.');
  if (dot == std::string::npos)
    return {name, ""};
  return {name.substr(0, dot), name.substr(dot + 1)};
}

std::string qualifyOp(const std::string &op, const std::string &form) {
  return form.empty() ? op : op + "." + form;
}

InstConfig readInstConfig(const JsonValue::Object &object) {
  InstConfig cfg;
  cfg.pipelineStartupCost = readIntField(object, "pipeline_startup_cost");
  cfg.latency = readIntField(object, "latency");
  cfg.throughput = readIntField(object, "throughput");
  cfg.pipelineDrainCost = readIntField(object, "pipeline_drain_cost");
  cfg.dataLoadCost = readIntField(object, "data_load_cost");
  cfg.dataStoreCost = readIntField(object, "data_store_cost");
  cfg.exu = readStringField(object, "EXU");
  cfg.dispatchExu = readStringField(object, "dispatch_exu");
  cfg.opClass = readStringField(object, "op_class");
  if (cfg.opClass.empty())
    cfg.opClass = readStringField(object, "class", readStringField(object, "category"));
  return cfg;
}

std::unordered_map<std::string, JsonValue> loadObjectFile(const std::filesystem::path &path) {
  JsonValue value = json::parseFile(path);
  if (!value.isObject())
    throw std::runtime_error("JSON root must be an object: " + path.string());
  return value.asObject();
}

std::filesystem::path pickPath(std::filesystem::path baseDir, const char *envKey,
                               std::initializer_list<std::filesystem::path> candidates) {
  if (const char *envPath = std::getenv(envKey); envPath && *envPath) {
    std::filesystem::path path(envPath);
    if (!std::filesystem::exists(path))
      throw std::runtime_error(std::string(envKey) + ": path not found: " + path.string());
    return std::filesystem::absolute(path);
  }

  for (const auto &candidate : candidates) {
    std::filesystem::path path = baseDir / candidate;
    if (std::filesystem::exists(path))
      return std::filesystem::absolute(path);
  }

  std::string message = std::string("Could not locate ") + envKey + ". Tried:";
  for (const auto &candidate : candidates)
    message += "\n  " + (baseDir / candidate).string();
  throw std::runtime_error(message);
}

std::optional<std::filesystem::path>
pickPathOptional(std::filesystem::path baseDir, const char *envKey,
                 std::initializer_list<std::filesystem::path> candidates) {
  if (const char *envPath = std::getenv(envKey); envPath && *envPath) {
    std::filesystem::path path(envPath);
    if (!std::filesystem::exists(path))
      throw std::runtime_error(std::string(envKey) + ": path not found: " + path.string());
    return std::filesystem::absolute(path);
  }

  for (const auto &candidate : candidates) {
    std::filesystem::path path = baseDir / candidate;
    if (std::filesystem::exists(path))
      return std::filesystem::absolute(path);
  }
  return std::nullopt;
}

} // namespace

ParamDB::ParamDB(std::filesystem::path baseDir)
    : baseDir_(resolveBaseDir(std::move(baseDir))) {
  const std::filesystem::path isaPath =
      pickPath(baseDir_, "ISA_JSON_PATH", {"configs/isa.json", "isa.json"});
  const std::filesystem::path uarchPath =
      pickPath(baseDir_, "UARCH_JSON_PATH", {"configs/uarch.json", "uarch.json"});
  const std::optional<std::filesystem::path> forwardingPath = pickPathOptional(
      baseDir_, "FORWARDING_JSON_PATH", {"configs/forwarding.json", "forwarding.json"});
  const std::optional<std::filesystem::path> iiPath = pickPathOptional(
      baseDir_, "II_JSON_PATH",
      {"configs/InitiationInterval.json", "Initiation_Interval.json"});

  const auto isaRoot = loadObjectFile(isaPath);
  const auto uarchRoot = loadObjectFile(uarchPath);
  const auto fwdRoot = forwardingPath ? loadObjectFile(*forwardingPath)
                                      : std::unordered_map<std::string, JsonValue>{};
  const auto iiRoot = iiPath ? loadObjectFile(*iiPath)
                             : std::unordered_map<std::string, JsonValue>{};

  if (const JsonValue *defaults = findKey(isaRoot, "defaults")) {
    if (!defaults->isObject())
      throw std::runtime_error("isa.json.defaults must be an object");
    const auto &obj = defaults->asObject();
    bundle_.isaDefaults.vfStartupCost = readIntField(obj, "vf_startup_cost");
    bundle_.isaDefaults.vfDrainCost = readIntField(obj, "vf_drain_cost");
  }

  if (const JsonValue *instructions = findKey(isaRoot, "instructions")) {
    if (!instructions->isObject())
      throw std::runtime_error("isa.json.instructions must be an object");
    for (const auto &[opName, opValue] : instructions->asObject()) {
      if (!opValue.isObject())
        throw std::runtime_error("isa.json.instructions." + opName +
                                 " must be an object");
      auto &dtypeMap = bundle_.isa[opName];
      const auto &opObject = opValue.asObject();
      const std::string inheritedOpClass =
          readStringField(opObject, "op_class",
                          readStringField(opObject, "class",
                                          readStringField(opObject, "category")));
      if (const JsonValue *forms = findKey(opObject, "forms")) {
        if (!forms->isObject())
          throw std::runtime_error("isa.json.instructions." + opName +
                                   ".forms must be an object");
        for (const auto &[formName, formValue] : forms->asObject()) {
          if (!formValue.isObject())
            throw std::runtime_error("isa.json.instructions." + opName +
                                     ".forms." + formName +
                                     " must be an object");
          InstConfig config = readInstConfig(formValue.asObject());
          if (config.opClass.empty())
            config.opClass = inheritedOpClass;
          dtypeMap.emplace(formName, std::move(config));
        }
        continue;
      }

      for (const auto &[dtypeName, dtypeValue] : opObject) {
        if (!dtypeValue.isObject())
          continue;
        InstConfig config = readInstConfig(dtypeValue.asObject());
        if (config.opClass.empty())
          config.opClass = inheritedOpClass;
        dtypeMap.emplace(dtypeName, std::move(config));
      }
    }
  }

  if (!uarchRoot.empty()) {
    const auto &obj = uarchRoot;
    bundle_.uarch.issuePorts = readIntField(obj, "issue_ports");
    bundle_.uarch.loadPorts = readIntField(obj, "load_ports");
    bundle_.uarch.storePorts = readIntField(obj, "store_ports");
    bundle_.uarch.iduWindowWidth = readIntField(obj, "IDU_window_width");
    bundle_.uarch.iduIssueWidth = readIntField(obj, "IDU_issue_width");
    bundle_.uarch.ldqWidth = readIntField(obj, "LDQ_width");
    bundle_.uarch.vregNum = readIntField(obj, "vreg_num");
    bundle_.uarch.enableIsuQueueModel = readBoolField(obj, "enable_isu_queue_model");
    bundle_.uarch.shqDepth = readIntField(obj, "shq_depth");
    bundle_.uarch.exqDepth = readIntField(obj, "exq_depth");
    bundle_.uarch.admitBlockedToExq = readBoolField(obj, "admit_blocked_to_exq");
    bundle_.uarch.enableShqCreditModel = readBoolField(obj, "enable_shq_credit_model");
    bundle_.uarch.shqReleaseDelay = readIntField(obj, "shq_release_delay");
    bundle_.uarch.enableCreditVisibilityDelay =
        readBoolField(obj, "enable_credit_visibility_delay");
    bundle_.uarch.iduVisiblePregDelay = readIntField(obj, "idu_visible_preg_delay");
    bundle_.uarch.iduVisibleShqDelay = readIntField(obj, "idu_visible_shq_delay");
    bundle_.uarch.globalShqPregGate = readBoolField(obj, "global_shq_preg_gate");
    bundle_.uarch.useExplicitIduCreditBank = readBoolField(obj, "use_explicit_idu_credit_bank");
    bundle_.uarch.iduToOooDelay = readIntField(obj, "idu_to_ooo_delay");
    bundle_.uarch.vloopToDispatchDelay = readIntField(obj, "vloop_to_dispatch_delay");
    bundle_.uarch.iduDispatchStartAdvance = readIntField(obj, "idu_dispatch_start_advance");
    bundle_.uarch.initialTopBlockVloopStartCycle =
        readIntField(obj, "initial_top_block_vloop_start_cycle");
    bundle_.uarch.nestedVloopInitialStartGap = readIntField(obj, "nested_vloop_initial_start_gap");
    bundle_.uarch.loop1MinFeedbackGap = readIntField(obj, "loop1_min_feedback_gap");
    bundle_.uarch.innermostIterDispatchStride =
        readIntField(obj, "innermost_iter_dispatch_stride");
    bundle_.uarch.consumerReleaseStartOffset = readIntField(obj, "consumer_release_start_offset");
    bundle_.uarch.loadDoneLatency = readIntField(obj, "load_done_latency");
    bundle_.uarch.oooToShqDelay = readIntField(obj, "ooo_to_shq_delay");
    bundle_.uarch.oooToLsqDelay = readIntField(obj, "ooo_to_lsq_delay");
    bundle_.uarch.exqRecvDelay = readIntField(obj, "exq_recv_delay");
    bundle_.uarch.shqToExqPortPerCycle = readIntField(obj, "shq_to_exq_port_per_cycle");
    bundle_.uarch.computeInflightCap = readIntField(obj, "compute_inflight_cap");
    bundle_.uarch.exqIssueInflightCapPerPort =
        readIntField(obj, "exq_issue_inflight_cap_per_port");
    bundle_.uarch.exqCapacityCountsInflight =
        readBoolField(obj, "exq_capacity_counts_inflight");
    bundle_.uarch.memBarMode = readStringField(obj, "mem_bar_mode");
    bundle_.uarch.enforceSameCycleSrcHazard =
        readBoolField(obj, "enforce_same_cycle_src_hazard");
    bundle_.uarch.enableCrossFuIi = readBoolField(obj, "enable_cross_fu_ii");
  }

  if (const JsonValue *forwarding = findKey(fwdRoot, "forwarding")) {
    if (!forwarding->isObject())
      throw std::runtime_error("forwarding.json.forwarding must be an object");
    for (const auto &[outerName, outerValue] : forwarding->asObject()) {
      if (!outerValue.isObject())
        throw std::runtime_error("forwarding.json.forwarding." + outerName +
                                 " must be an object");
      const auto [prodOp, prodDtype] = splitQualifiedOp(outerName);
      if (!prodDtype.empty()) {
        auto &consMap = bundle_.forwarding[prodDtype][prodOp];
        for (const auto &[consName, consValue] : outerValue.asObject()) {
          const auto [consOp, consDtype] = splitQualifiedOp(consName);
          bundle_.forwardingByForm[outerName][consName] = consValue.asInt();
          if (consDtype.empty() || consDtype == prodDtype)
            consMap.emplace(consOp, consValue.asInt());
        }
        continue;
      }

      auto &prodMap = bundle_.forwarding[outerName];
      for (const auto &[prodName, prodValue] : outerValue.asObject()) {
        if (!prodValue.isObject())
          throw std::runtime_error("forwarding.json.forwarding." + outerName +
                                   "." + prodName + " must be an object");
        auto &consMap = prodMap[prodName];
        for (const auto &[consName, consValue] : prodValue.asObject())
          consMap.emplace(consName, consValue.asInt());
      }
    }
  }

  if (const JsonValue *ii = findKey(iiRoot, "InitiationInterval")) {
    if (!ii->isObject())
      throw std::runtime_error("InitiationInterval.json.InitiationInterval must be an object");
    for (const auto &[outerName, outerValue] : ii->asObject()) {
      if (!outerValue.isObject())
        throw std::runtime_error("InitiationInterval.json.InitiationInterval." +
                                 outerName + " must be an object");
      const auto [prevOp, prevDtype] = splitQualifiedOp(outerName);
      if (!prevDtype.empty()) {
        auto &curMap = bundle_.initiationInterval[prevDtype][prevOp];
        for (const auto &[curName, curValue] : outerValue.asObject()) {
          const auto [curOp, curDtype] = splitQualifiedOp(curName);
          bundle_.initiationIntervalByForm[outerName][curName] = curValue.asInt();
          if (curDtype.empty() || curDtype == prevDtype)
            curMap.emplace(curOp, curValue.asInt());
        }
        continue;
      }

      auto &prevMap = bundle_.initiationInterval[outerName];
      for (const auto &[prevName, prevValue] : outerValue.asObject()) {
        if (!prevValue.isObject())
          throw std::runtime_error("InitiationInterval.json.InitiationInterval." +
                                   outerName + "." + prevName + " must be an object");
        auto &curMap = prevMap[prevName];
        for (const auto &[curName, curValue] : prevValue.asObject())
          curMap.emplace(curName, curValue.asInt());
      }
    }
  }
}

std::filesystem::path ParamDB::resolveBaseDir(std::filesystem::path baseDir) {
  if (baseDir.empty())
    return std::filesystem::absolute(std::filesystem::current_path());
  return std::filesystem::absolute(std::move(baseDir));
}

bool ParamDB::hasInst(const std::string &op, const std::string &dtype) const {
  const auto opIt = bundle_.isa.find(op);
  if (opIt == bundle_.isa.end())
    return false;
  return opIt->second.find(dtype) != opIt->second.end();
}

const InstConfig &ParamDB::inst(const std::string &op, const std::string &dtype) const {
  const auto opIt = bundle_.isa.find(op);
  if (opIt == bundle_.isa.end())
    throw std::runtime_error("Instruction not found: op=" + op + ", dtype=" + dtype);
  const auto dtypeIt = opIt->second.find(dtype);
  if (dtypeIt == opIt->second.end())
    throw std::runtime_error("Instruction not found: op=" + op + ", dtype=" + dtype);
  return dtypeIt->second;
}

int64_t ParamDB::forwardingCycles(const std::string &dtype, const std::string &prod,
                                  const std::string &cons) const {
  const auto dtypeIt = bundle_.forwarding.find(dtype);
  if (dtypeIt != bundle_.forwarding.end()) {
    const auto prodIt = dtypeIt->second.find(prod);
    if (prodIt != dtypeIt->second.end()) {
      const auto consIt = prodIt->second.find(cons);
      if (consIt != prodIt->second.end())
        return std::max<int64_t>(0, consIt->second);
    }
  }
  const int64_t latency = hasInst(prod, dtype) ? inst(prod, dtype).latency : 0;
  return std::max<int64_t>(0, latency - 3);
}

int64_t ParamDB::forwardingCycles(const std::string &prod,
                                  const std::string &prodForm,
                                  const std::string &cons,
                                  const std::string &consForm) const {
  const auto prodIt = bundle_.forwardingByForm.find(qualifyOp(prod, prodForm));
  if (prodIt != bundle_.forwardingByForm.end()) {
    const auto consIt = prodIt->second.find(qualifyOp(cons, consForm));
    if (consIt != prodIt->second.end())
      return std::max<int64_t>(0, consIt->second);
  }
  if (prodForm == consForm)
    return forwardingCycles(prodForm, prod, cons);
  const int64_t latency =
      hasInst(prod, prodForm) ? inst(prod, prodForm).latency : 0;
  return std::max<int64_t>(0, latency - 3);
}

int64_t ParamDB::initiationInterval(const std::string &dtype, const std::string &prev,
                                    const std::string &cur) const {
  const auto dtypeIt = bundle_.initiationInterval.find(dtype);
  if (dtypeIt != bundle_.initiationInterval.end()) {
    const auto prevIt = dtypeIt->second.find(prev);
    if (prevIt != dtypeIt->second.end()) {
      const auto curIt = prevIt->second.find(cur);
      if (curIt != prevIt->second.end())
        return std::max<int64_t>(1, curIt->second);
    }
  }
  return 1;
}

int64_t ParamDB::initiationInterval(const std::string &prev,
                                    const std::string &prevForm,
                                    const std::string &cur,
                                    const std::string &curForm) const {
  const auto prevIt =
      bundle_.initiationIntervalByForm.find(qualifyOp(prev, prevForm));
  if (prevIt != bundle_.initiationIntervalByForm.end()) {
    const auto curIt = prevIt->second.find(qualifyOp(cur, curForm));
    if (curIt != prevIt->second.end())
      return std::max<int64_t>(1, curIt->second);
  }
  if (prevForm == curForm)
    return initiationInterval(prevForm, prev, cur);
  return 1;
}

} // namespace vfsim
