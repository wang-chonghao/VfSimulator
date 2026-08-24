// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IFU.h"
#include "native/IDU.h"
#include "native/OOO.h"
#include "native/ParamDB.h"
#include "native/ProgramAnalysis.h"
#include "native/ProgramFlatten.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <fstream>
#include <atomic>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

namespace {

using namespace vfsim;

void require(bool cond, const std::string &msg) {
  if (!cond)
    throw std::runtime_error(msg);
}

ProgramInstNode makeInst(std::string op, std::vector<std::string> dst,
                         std::vector<std::string> src) {
  ProgramInstNode inst;
  inst.op = std::move(op);
  inst.dst = std::move(dst);
  inst.src = std::move(src);
  return inst;
}

ProgramNode makeInstNode(const std::string &op, std::vector<std::string> dst,
                         std::vector<std::string> src) {
  return ProgramNode::makeInst(makeInst(op, std::move(dst), std::move(src)));
}

ProgramNode makeMembarNode(std::string barrier) {
  ProgramMembarNode membar;
  membar.barrier = std::move(barrier);
  return ProgramNode::makeMembar(std::move(membar));
}

ProgramNode makeLoopNode(std::string iters, std::vector<ProgramNode> body) {
  ProgramLoopNode loop;
  loop.iters = std::move(iters);
  loop.unroll = "1";
  loop.name = "top_loop";
  loop.body = std::move(body);
  return ProgramNode::makeLoop(std::move(loop));
}

ProgramNode makeUnrolledLoopNode(std::string iters, std::string unroll,
                                 std::vector<ProgramNode> body) {
  ProgramLoopNode loop;
  loop.iters = std::move(iters);
  loop.unroll = std::move(unroll);
  loop.name = "unroll_order_loop";
  loop.body = std::move(body);
  return ProgramNode::makeLoop(std::move(loop));
}

std::vector<ProgramNode> buildTaddTmulProgram() {
  std::vector<ProgramNode> body;

  body.push_back(makeLoopNode(
      "1",
      {
          makeInstNode("VADDS", {"v2"}, {"v0", "v1"}),
      }));

  return body;
}

int countTopLevelLoops(const std::vector<ProgramNode> &program) {
  int count = 0;
  for (const auto &node : program)
    if (node.kind == ProgramNode::Kind::Loop)
      ++count;
  return count;
}

void verifyUnrollOrder(const ParamDB &db) {
  const std::vector<ProgramNode> program = {makeUnrolledLoopNode(
      "2", "2",
      {makeInstNode("VLDS", {"v0"}, {"mem0"}),
       makeInstNode("VADD", {"v1"}, {"v0", "v2"}),
       makeInstNode("VLDS", {"v3"}, {"mem1"}),
       makeInstNode("VSUB", {"v4"}, {"v1", "v3"}),
       makeInstNode("VSTS", {"mem2"}, {"v4"})})};

  ProgramFlatten flattener;
  const auto &linear = flattener.flatten(program);
  ProgramAnalysis analysis;
  IFU ifu(linear, {}, &db, analysis.inferTopBlockLoopBounds(program), 1,
          "fp32");
  const auto emitted = ifu.take(10);
  const std::vector<std::string> expected = {
      "VLDS", "VLDS", "VADD", "VADD", "VLDS",
      "VLDS", "VSUB", "VSUB", "VSTS", "VSTS"};
  require(emitted.size() == expected.size(),
          "unrolled IFU emitted an unexpected instruction count");
  for (size_t i = 0; i < expected.size(); ++i) {
    require(emitted[i].op == expected[i],
            "unrolled IFU must preserve static AABBCC order");
    require(emitted[i].lane == static_cast<int64_t>(i % 2),
            "unrolled IFU emitted an unexpected lane order");
  }
}

std::string readText(const std::filesystem::path &path) {
  std::ifstream in(path);
  std::ostringstream buffer;
  buffer << in.rdbuf();
  return buffer.str();
}

void writeText(const std::filesystem::path &path, const std::string &text) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream out(path);
  out << text;
}

int64_t cycleForInstId(const std::string &jsonl, int64_t instId) {
  const std::string needle = "\"inst_id\":" + std::to_string(instId);
  std::istringstream lines(jsonl);
  std::string line;
  while (std::getline(lines, line)) {
    if (line.find(needle) == std::string::npos)
      continue;
    const std::string cyNeedle = "\"cy\":";
    const auto pos = line.find(cyNeedle);
    if (pos == std::string::npos)
      break;
    const auto start = pos + cyNeedle.size();
    const auto end = line.find_first_of(",}", start);
    return std::stoll(line.substr(start, end - start));
  }
  throw std::runtime_error("inst_id not found in jsonl: " + std::to_string(instId));
}

void verifyNativeExu0ReserveDispatch(const ParamDB &db) {
  require(db.uarch().shqExqDispatchPolicy == "fu_round_robin_exu0_reserve",
          "native uarch must load SHQ-to-EXQ dispatch policy");
  require(db.uarch().exu0ReserveLookahead == 8,
          "native EXU0 reserve lookahead mismatch");
  require(db.uarch().exu0ReserveMinCount == 1,
          "native EXU0 reserve min_count mismatch");

  OoOCoreMainline core(db.uarch(), db, "fp32");
  DynamicInst flexible;
  flexible.type = "inst";
  flexible.instId = 9000;
  flexible.op = "VADDS";
  flexible.form = "fp32";
  DynamicInst exu0Only;
  exu0Only.type = "inst";
  exu0Only.instId = 9001;
  exu0Only.op = "VPACK";
  exu0Only.form = "b32";
  core.accept(flexible);
  core.accept(exu0Only);

  for (int cycle = 0; cycle < 32; ++cycle) {
    core.step();
    if (core.getExuPortForInst(9000).has_value() &&
        core.getExuPortForInst(9001).has_value())
      break;
  }
  require(core.getExuPortForInst(9000) == std::optional<int>{1},
          "flexible ALU must reserve EXQ0 for pending EXU0_ONLY work");
  require(core.getExuPortForInst(9001) == std::optional<int>{0},
          "EXU0_ONLY work must dispatch to EXQ0");

  OoOCoreMainline balancedCore(db.uarch(), db, "fp32");
  DynamicInst firstFlexible;
  firstFlexible.type = "inst";
  firstFlexible.instId = 9010;
  firstFlexible.op = "VADDS";
  firstFlexible.form = "fp32";
  firstFlexible.dst = {"v_first"};
  DynamicInst firstDependentExu0Only;
  firstDependentExu0Only.type = "inst";
  firstDependentExu0Only.instId = 9011;
  firstDependentExu0Only.op = "VPACK";
  firstDependentExu0Only.form = "b32";
  firstDependentExu0Only.src = {"v_first"};
  firstDependentExu0Only.dst = {"v_first_pack"};
  balancedCore.accept(firstFlexible);
  balancedCore.accept(firstDependentExu0Only);
  balancedCore.step();

  DynamicInst secondFlexible;
  secondFlexible.type = "inst";
  secondFlexible.instId = 9012;
  secondFlexible.op = "VADDS";
  secondFlexible.form = "fp32";
  secondFlexible.dst = {"v_second"};
  DynamicInst secondDependentExu0Only;
  secondDependentExu0Only.type = "inst";
  secondDependentExu0Only.instId = 9013;
  secondDependentExu0Only.op = "VPACK";
  secondDependentExu0Only.form = "b32";
  secondDependentExu0Only.src = {"v_second"};
  secondDependentExu0Only.dst = {"v_second_pack"};
  balancedCore.accept(secondFlexible);
  balancedCore.accept(secondDependentExu0Only);

  for (int cycle = 0; cycle < 64; ++cycle) {
    balancedCore.step();
    if (balancedCore.getExuPortForInst(9012).has_value())
      break;
  }
  require(balancedCore.getExuPortForInst(9010) == std::optional<int>{1},
          "first flexible work must establish one EXQ1 reserve slot");
  require(balancedCore.getExuPortForInst(9012) == std::optional<int>{0},
          "balanced reserve must return flexible work to EXQ0 once the target gap is reached");
}

void verifyNativeThreePortsMode(const ParamDB &db) {
  UarchConfig uarch = db.uarch();
  uarch.issuePorts = 3;
  uarch.threePortsMode = true;
  OoOCoreMainline core(uarch, db, "fp32");
  require(core.getEligibleExuPorts("VADD", "fp32") ==
              std::vector<int>({0, 1, 2}),
          "native three_ports_mode must allow EXU01 on ports 0/1/2");
  require(core.getEligibleExuPorts("VPACK", "b32") ==
              std::vector<int>({0}),
          "native three_ports_mode must preserve EXU0_ONLY routing");
}

void verifyNativeUbSharedSlotsAndPregPressure(const ParamDB &db) {
  auto runCase = [&](int64_t vregNum, const std::string &suffix) {
    UarchConfig uarch = db.uarch();
    uarch.vregNum = vregNum;
    uarch.loadPorts = 2;
    uarch.storePorts = 1;
    uarch.ubSlots = 2;
    uarch.lsuStorePriorityPregThreshold = 1;
    OoOCoreMainline core(uarch, db, "fp32");

    DynamicInst producer;
    producer.type = "inst";
    producer.instId = 9200;
    producer.streamSeq = 0;
    producer.op = "VADD";
    producer.form = "fp32";
    producer.dst = {"v0"};
    core.accept(producer);
    for (int cycle = 0; cycle < 40; ++cycle)
      core.step();

    DynamicInst store;
    store.type = "inst";
    store.instId = 9201;
    store.streamSeq = 1;
    store.op = "VSTS";
    store.form = "fp32";
    store.src = {"v0"};
    store.dst = {"mem0"};
    DynamicInst firstLoad;
    firstLoad.type = "inst";
    firstLoad.instId = 9202;
    firstLoad.streamSeq = 2;
    firstLoad.op = "VLDS";
    firstLoad.form = "fp32";
    firstLoad.src = {"mem1"};
    firstLoad.dst = {"v1"};
    DynamicInst secondLoad = firstLoad;
    secondLoad.instId = 9203;
    secondLoad.streamSeq = 3;
    secondLoad.dst = {"v2"};
    core.accept(store);
    core.accept(firstLoad);
    core.accept(secondLoad);
    for (int cycle = 0; cycle < 20; ++cycle)
      core.step();

    const auto root = std::filesystem::temp_directory_path() /
                      ("vfsim_native_ub_slots_" + suffix);
    std::filesystem::create_directories(root);
    core.dumpSimpleLogs((root / "starts.jsonl").string(),
                        (root / "done.jsonl").string());
    const std::string starts = readText(root / "starts.jsonl");
    return std::make_tuple(cycleForInstId(starts, 9201),
                           cycleForInstId(starts, 9202),
                           cycleForInstId(starts, 9203));
  };

  const auto relaxed = runCase(16, "relaxed");
  require(std::get<1>(relaxed) == std::get<2>(relaxed),
          "two ready loads must share both UB slots when pregs remain");
  require(std::get<0>(relaxed) > std::get<1>(relaxed),
          "ready loads must take priority over an older store when pregs remain");

  const auto pressured = runCase(1, "pressured");
  require(std::get<0>(pressured) == std::get<1>(pressured),
          "preg pressure must issue one store and one load through shared UB slots");
  require(std::get<2>(pressured) > std::get<1>(pressured),
          "the second load must wait when a store consumes one shared UB slot");
}

void verifyNativeVectorAlignGenerations(const ParamDB &db) {
  OoOCoreMainline core(db.uarch(), db, "fp32");

  DynamicInst reduction;
  reduction.instId = 9300;
  reduction.streamSeq = 0;
  reduction.op = "VCMAX";
  reduction.form = "fp32";
  reduction.dst = {"v0"};

  auto makeVstus = [](int64_t id, int64_t seq, const std::string &memory) {
    DynamicInst inst;
    inst.instId = id;
    inst.streamSeq = seq;
    inst.op = "VSTUS";
    inst.form = "fp32";
    inst.src = {"v0"};
    inst.dst = {memory};
    inst.alignStateOperation = "append";
    inst.alignStateId = "u1";
    return inst;
  };
  auto makeVstas = [](int64_t id, int64_t seq, const std::string &memory) {
    DynamicInst inst;
    inst.instId = id;
    inst.streamSeq = seq;
    inst.op = "VSTAS";
    inst.form = "fp32";
    inst.dst = {memory};
    inst.alignStateOperation = "consume";
    inst.alignStateId = "u1";
    return inst;
  };

  core.accept(reduction);
  core.accept(makeVstus(9301, 1, "mem0"));
  core.accept(makeVstas(9302, 2, "mem0"));
  core.accept(makeVstus(9303, 3, "mem1"));
  core.accept(makeVstas(9304, 4, "mem1"));
  for (int cycle = 0; cycle < 100; ++cycle)
    core.step();

  const auto root = std::filesystem::temp_directory_path() /
                    "vfsim_native_vector_align";
  std::filesystem::create_directories(root);
  core.dumpSimpleLogs((root / "starts.jsonl").string(),
                      (root / "done.jsonl").string());
  const std::string starts = readText(root / "starts.jsonl");
  const int64_t firstProducer = cycleForInstId(starts, 9301);
  const int64_t firstConsumer = cycleForInstId(starts, 9302);
  const int64_t secondProducer = cycleForInstId(starts, 9303);
  const int64_t secondConsumer = cycleForInstId(starts, 9304);
  require(firstConsumer == firstProducer + 1,
          "VSTAS must consume only its sealed generation at forwarding + 1");
  require(firstConsumer < secondProducer,
          "a later VSTUS must not pollute the preceding sealed generation");
  require(secondConsumer == secondProducer + 1,
          "the next VSTAS must wait for its own VSTUS generation");
}

ParamDB makeDurationTestDb() {
  const auto root = std::filesystem::temp_directory_path() / "vfsim_native_duration_cfg";
  writeText(
      root / "configs" / "isa.json",
      R"JSON({
        "defaults": {"vf_startup_cost": 0, "vf_drain_cost": 0},
        "instructions": {
          "VLDS": {
            "op_class": "LOAD",
            "forms": {
              "fp32": {"latency": 5, "pipeline_startup_cost": 0, "pipeline_drain_cost": 0, "data_load_cost": 99, "data_store_cost": 1}
            }
          },
          "VSTS": {
            "op_class": "STORE",
            "forms": {
              "fp32": {"latency": 7, "pipeline_startup_cost": 0, "pipeline_drain_cost": 0, "data_load_cost": 1, "data_store_cost": 99}
            }
          }
        }
      })JSON");
  writeText(
      root / "configs" / "uarch.json",
      R"JSON({
        "issue_ports": 2,
        "load_ports": 2,
        "store_ports": 1,
        "IDU_window_width": 8,
        "IDU_issue_width": 5,
        "LDQ_width": 8,
        "vreg_num": 16,
        "ooo_to_shq_delay": 1,
        "ooo_to_lsq_delay": 1,
        "idu_to_ooo_delay": 0,
        "consumer_release_start_offset": 0,
        "enable_cross_fu_ii": true
      })JSON");
  return ParamDB(root);
}

ParamDB makePartialCompatTestDb() {
  const auto root = std::filesystem::temp_directory_path() / "vfsim_native_partial_compat_cfg";
  writeText(
      root / "configs" / "isa.json",
      R"JSON({
        "defaults": {"vf_startup_cost": 0, "vf_drain_cost": 0},
        "instructions": {
          "VPARTIAL": {
            "op_class": "COMPUTE",
            "forms": {
              "fp16": {
                "latency": 7,
                "throughput": 2,
                "data_load_cost": 9,
                "data_store_cost": 9,
                "EXU": "ALU",
                "dispatch_exu": "EXU01"
              },
              "b16": {"latency": 123}
            }
          },
          "VFP32ONLY": {
            "op_class": "COMPUTE",
            "forms": {
              "fp32": {
                "latency": 123,
                "EXU": "SFU",
                "dispatch_exu": "EXU0_ONLY"
              }
            }
          }
        }
      })JSON");
  writeText(
      root / "configs" / "uarch.json",
      R"JSON({
        "issue_ports": 2,
        "load_ports": 2,
        "store_ports": 1,
        "IDU_window_width": 8,
        "IDU_issue_width": 5,
        "LDQ_width": 8,
        "vreg_num": 16
      })JSON");
  return ParamDB(root);
}

void verifyInstructionFallback(ParamDB &db) {
  const InstConfig &unknown = db.inst("VUNKNOWN_NATIVE", "fp32");
  require(unknown.opClass == "COMPUTE", "unknown native op must fallback to compute");
  require(unknown.latency == 9, "unknown native op latency fallback mismatch");
  require(unknown.dispatchExu == "EXU01", "unknown native op dispatch fallback mismatch");
  const InstConfig &load = db.inst("VLDX_NATIVE", "fp32");
  const InstConfig &store = db.inst("VSTX_NATIVE", "fp32");
  require(load.opClass == "LOAD", "unknown VLD* must fallback to LOAD");
  require(store.opClass == "STORE", "unknown VST* must fallback to STORE");
  require(db.forwardingCycles("VLDX_NATIVE", "fp32", "VSTX_NATIVE", "fp32") == 6,
          "native load-store forwarding fallback mismatch");
  bool sawUnsupported = false;
  bool sawForwarding = false;
  for (const auto &warning : db.warnings()) {
    sawUnsupported = sawUnsupported || warning.kind == "unsupported_isa_op";
    sawForwarding = sawForwarding || warning.kind == "missing_forwarding_pair";
  }
  require(sawUnsupported, "native fallback must record unsupported_isa_op");
  require(sawForwarding, "native fallback must record missing_forwarding_pair");
}

void verifySharedParamDbFallbackQueriesAreStable(ParamDB &db) {
  static_assert(
      !std::is_reference_v<decltype(db.inst("VTHREAD_UNKNOWN", "fp32"))>,
      "ParamDB::inst must return fallback-safe values");
  std::atomic<bool> valid{true};
  std::vector<std::thread> workers;
  for (int threadIndex = 0; threadIndex < 8; ++threadIndex) {
    workers.emplace_back([&]() {
      for (int iteration = 0; iteration < 200; ++iteration) {
        const InstConfig cfg = db.inst("VTHREAD_UNKNOWN", "fp32");
        if (cfg.opClass != "COMPUTE" || cfg.latency != 9)
          valid.store(false);
        if (db.forwardingCycles("VTHREAD_PROD", "fp32", "VTHREAD_CONS",
                                "fp32") < 0)
          valid.store(false);
        if (db.initiationInterval("VTHREAD_PREV", "fp32", "VTHREAD_CUR",
                                  "fp32") < 1)
          valid.store(false);
      }
    });
  }
  for (auto &worker : workers)
    worker.join();
  require(valid.load(), "shared ParamDB fallback queries returned invalid data");
  bool sawThreadWarning = false;
  for (const auto &warning : db.warnings())
    if (warning.kind == "unsupported_isa_op" &&
        warning.fields.count("op") &&
        warning.fields.at("op") == "VTHREAD_UNKNOWN") {
      sawThreadWarning = warning.count == 1;
    }
  require(sawThreadWarning,
          "shared ParamDB warning aggregation must be deterministic");
}

void verifyNativePartialCompatibleFormMerge() {
  ParamDB db = makePartialCompatTestDb();
  const InstConfig &partial = db.inst("VPARTIAL", "b16");
  require(partial.latency == 123, "native partial b16 latency override mismatch");
  require(partial.throughput == 2, "native partial b16 throughput must inherit fp16");
  require(partial.dataLoadCost == 9, "native partial b16 data_load_cost must inherit fp16");
  require(partial.dataStoreCost == 9, "native partial b16 data_store_cost must inherit fp16");
  require(partial.dispatchExu == "EXU01", "native partial b16 dispatch_exu must inherit fp16");

  const InstConfig &missingFp16 = db.inst("VFP32ONLY", "fp16");
  require(missingFp16.latency == 9,
          "native missing fp16 form must not borrow fp32 latency");
  require(missingFp16.dispatchExu == "EXU01",
          "native missing fp16 form must use compute fallback dispatch");
  bool sawUnsupportedForm = false;
  for (const auto &warning : db.warnings())
    sawUnsupportedForm =
        sawUnsupportedForm || warning.kind == "unsupported_isa_form";
  require(sawUnsupportedForm,
          "native missing fp16 form must record unsupported_isa_form");
}

void verifyVpackVsstbConfig(const ParamDB &db) {
  const InstConfig &vpack = db.inst("VPACK", "b32");
  const InstConfig &vsstb = db.inst("VSSTB", "b16");
  require(vpack.opClass == "COMPUTE", "VPACK must be modeled as compute");
  require(vpack.latency == 11, "VPACK latency mismatch");
  require(vpack.dispatchExu == "EXU0_ONLY", "VPACK must dispatch to EXU0 only");
  require(vsstb.opClass == "STORE", "VSSTB must be modeled as store");
  require(vsstb.latency == 9, "VSSTB latency mismatch");
  require(vsstb.dataStoreCost == 9, "VSSTB data store cost mismatch");
  require(db.forwardingCycles("VCVT_F32_TO_F16", "f32_to_f16", "VPACK",
                              "b32") == 4,
          "VCVT_F32_TO_F16 -> VPACK forwarding mismatch");
  require(db.forwardingCycles("VPACK", "b32", "VADD", "fp16") == 8,
          "VPACK -> VADD(fp16) forwarding mismatch");
  require(db.forwardingCycles("VPACK", "b32", "VSSTB", "b16") == 9,
          "VPACK -> VSSTB forwarding mismatch");
}

void verifyVexpdifVmulscvtConfig(const ParamDB &db) {
  const InstConfig &vexpdif = db.inst("VEXPDIF", "fp32");
  const InstConfig &vmulscvt = db.inst("VMULSCVT", "f32_to_f16");
  require(vexpdif.opClass == "COMPUTE", "VEXPDIF must be modeled as compute");
  require(vexpdif.pipelineStartupCost == 7, "VEXPDIF startup mismatch");
  require(vexpdif.latency == 18, "VEXPDIF latency mismatch");
  require(vexpdif.pipelineDrainCost == 16, "VEXPDIF drain mismatch");
  require(vexpdif.dataLoadCost == 9, "VEXPDIF load cost mismatch");
  require(vexpdif.dataStoreCost == 9, "VEXPDIF store cost mismatch");
  require(vexpdif.dispatchExu == "EXU01", "VEXPDIF dispatch mismatch");
  require(vmulscvt.opClass == "COMPUTE", "VMULSCVT must be modeled as compute");
  require(vmulscvt.pipelineStartupCost == 6, "VMULSCVT startup mismatch");
  require(vmulscvt.latency == 8, "VMULSCVT latency mismatch");
  require(vmulscvt.pipelineDrainCost == 6, "VMULSCVT drain mismatch");
  require(vmulscvt.dataLoadCost == 9, "VMULSCVT load cost mismatch");
  require(vmulscvt.dataStoreCost == 9, "VMULSCVT store cost mismatch");
  require(vmulscvt.dispatchExu == "EXU01", "VMULSCVT dispatch mismatch");
  require(db.forwardingCycles("VMULS", "fp32", "VEXPDIF", "fp32") == 5,
          "VMULS -> VEXPDIF forwarding mismatch");
  require(db.forwardingCycles("VEXPDIF", "fp32", "VMULSCVT", "f32_to_f16") == 15,
          "VEXPDIF -> VMULSCVT forwarding mismatch");
  require(db.forwardingCycles("VMULSCVT", "f32_to_f16", "VPACK", "b32") == 5,
          "VMULSCVT -> VPACK forwarding mismatch");
  require(db.initiationInterval("VEXPDIF", "fp32", "VEXPDIF", "fp32") == 4,
          "VEXPDIF -> VEXPDIF II mismatch");
}

void verifyBf16ConvertStoreConfig(const ParamDB &db) {
  const InstConfig &vcvt = db.inst("VCVT_F32_TO_BF16", "f32_to_bf16");
  const InstConfig &vsts = db.inst("VSTS", "bf16");
  require(vcvt.opClass == "COMPUTE", "VCVT_F32_TO_BF16 must be modeled as compute");
  require(vcvt.pipelineStartupCost == 6, "VCVT_F32_TO_BF16 startup mismatch");
  require(vcvt.latency == 7, "VCVT_F32_TO_BF16 latency mismatch");
  require(vcvt.pipelineDrainCost == 5, "VCVT_F32_TO_BF16 drain mismatch");
  require(vcvt.dispatchExu == "EXU01", "VCVT_F32_TO_BF16 dispatch mismatch");
  require(vsts.opClass == "STORE", "VSTS.bf16 must be modeled as store");
  require(vsts.pipelineStartupCost == 8, "VSTS.bf16 startup mismatch");
  require(vsts.latency == 9, "VSTS.bf16 latency mismatch");
  require(vsts.pipelineDrainCost == 0, "VSTS.bf16 drain mismatch");
  require(db.forwardingCycles("VEXP", "fp32", "VCVT_F32_TO_BF16",
                              "f32_to_bf16") == 13,
          "VEXP -> VCVT_F32_TO_BF16 forwarding mismatch");
  require(db.forwardingCycles("VCVT_F32_TO_BF16", "f32_to_bf16", "VSTS",
                              "bf16") == 5,
          "VCVT_F32_TO_BF16 -> VSTS.bf16 forwarding mismatch");
  require(db.forwardingCycles("VCVT_F32_TO_BF16", "f32_to_bf16", "VPACK",
                              "b32") == 4,
          "VCVT_F32_TO_BF16 -> VPACK forwarding mismatch");
  require(db.forwardingCycles("VEXPDIF", "fp32", "VADD", "fp32") == 15,
          "VEXPDIF -> VADD forwarding mismatch");
}

void verifyCompatibleFormFallback(ParamDB &db) {
  const InstConfig &vaddB16 = db.inst("VADD", "b16");
  const InstConfig &vaddFp16 = db.inst("VADD", "fp16");
  require(db.hasInst("VADD", "b16"), "native compatible form must satisfy hasInst");
  require(vaddB16.latency == vaddFp16.latency,
          "native b16 ISA params must fall back to fp16");
  require(db.forwardingCycles("VADD", "b16", "VMUL", "b16") ==
              db.forwardingCycles("VADD", "fp16", "VMUL", "fp16"),
          "native b16 forwarding pair must fall back to fp16");
  require(db.initiationInterval("VADD", "b16", "VMUL", "b16") ==
              db.initiationInterval("VADD", "fp16", "VMUL", "fp16"),
          "native b16 II pair must fall back to fp16");

  bool sawIsa = false;
  bool sawForwarding = false;
  bool sawIi = false;
  for (const auto &warning : db.warnings()) {
    sawIsa = sawIsa || warning.kind == "compatible_isa_form_fallback";
    sawForwarding = sawForwarding || warning.kind == "compatible_forwarding_pair_fallback";
    sawIi = sawIi || warning.kind == "compatible_ii_pair_fallback";
  }
  require(sawIsa, "native compatible ISA fallback must record warning");
  require(sawForwarding, "native compatible forwarding fallback must record warning");
  require(sawIi, "native compatible II fallback must record warning");
}

void verifyNativeVpackVsstbScheduling(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  ValueInfo input;
  input.valueId = "memA";
  input.storage = ValueStorageKind::UB;
  input.dtype = "fp16";
  ValueInfo output;
  output.valueId = "memB";
  output.storage = ValueStorageKind::UB;
  output.dtype = "bf16";
  ValueInfo loaded;
  loaded.valueId = "v0";
  loaded.storage = ValueStorageKind::Register;
  loaded.dtype = "fp16";
  ValueInfo packed;
  packed.valueId = "v1";
  packed.storage = ValueStorageKind::Register;
  packed.dtype = "bf16";
  vfInfo.values = {{"memA", input}, {"memB", output}, {"v0", loaded}, {"v1", packed}};
  ProgramNode vlds = makeInstNode("VLDS", {"v0"}, {"memA"});
  vlds.inst.form = "fp16";
  ProgramNode vpack = makeInstNode("VPACK", {"v1"}, {"v0"});
  vpack.inst.form = "b32";
  ProgramNode vsstb = makeInstNode("VSSTB", {"memB"}, {"v1"});
  vsstb.inst.form = "b16";
  vfInfo.body = {makeLoopNode("1", {vlds, vpack, vsstb})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_vpack_vsstb";
  const auto result = runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  require(result.vfEndCycle > 0, "native VPACK/VSSTB scheduling did not complete");
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string warnings = readText(outDir / "model_warnings.json");
  require(starts.find("\"op\":\"VPACK\"") != std::string::npos,
          "native VPACK must reach execution start log");
  require(starts.find("\"op\":\"VSSTB\"") != std::string::npos,
          "native VSSTB must reach execution start log");
  require(warnings.find("VPACK.fp32") == std::string::npos,
          "native VPACK must not be looked up through global fp32 form");
  require(warnings.find("VSSTB.fp32") == std::string::npos,
          "native VSSTB must not be looked up through global fp32 form");
}

void verifyMembarDisablesUnroll(ParamDB &db) {
  const std::vector<ProgramNode> program = {makeUnrolledLoopNode(
      "4", "2",
      {makeInstNode("VLDS", {"v0"}, {"mem0"}),
       makeInstNode("VSTS", {"mem1"}, {"v0"}),
       makeMembarNode("VST_VLD"),
       makeInstNode("VLDS", {"v1"}, {"mem2"})})};
  ProgramFlatten flattener;
  const auto &linear = flattener.flatten(program);
  ProgramAnalysis analysis;
  IFU ifu(linear, {}, &db, analysis.inferTopBlockLoopBounds(program), 1,
          "fp32");
  const auto emitted = ifu.take(12);
  require(emitted.size() == 12, "membar unroll-disabled IFU instruction count mismatch");
  for (const auto &inst : emitted) {
    require(inst.lane == -1, "membar loop should not emit unroll lanes");
    for (const auto &src : inst.src)
      require(src.find("_lane") == std::string::npos, "membar loop emitted lane src");
    for (const auto &dst : inst.dst)
      require(dst.find("_lane") == std::string::npos, "membar loop emitted lane dst");
  }
  bool saw = false;
  for (const auto &warning : db.warnings())
    saw = saw || warning.kind == "membar_unroll_disabled";
  require(saw, "native IFU must record membar_unroll_disabled");
}

void verifyExplicitMembarTiming(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("VLDS", {"v0"}, {"memA"}),
       makeInstNode("VSTS", {"memB"}, {"v0"}),
       makeMembarNode("MEMBAR.VST_VLD"),
       makeInstNode("VLDS", {"v1"}, {"memC"}),
       makeInstNode("VADDS", {"v2"}, {"v0"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_membar";
  const auto result = runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  require(result.vfEndCycle > 0, "native membar run did not complete");
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  const std::string history = readText(outDir / "sim_history.json");
  const int64_t storeDone = cycleForInstId(dones, 1);
  const int64_t postBarrierLoadStart = cycleForInstId(starts, 2);
  const int64_t computeStart = cycleForInstId(starts, 3);
  require(postBarrierLoadStart >= storeDone,
          "native VST_VLD must block following load until prior store done");
  require(computeStart < storeDone,
          "native VST_VLD must not cause IDU head-of-line blocking for compute");
  require(history.find("\"blocked_reason\":\"membar\"") != std::string::npos,
          "native membar issue gate must write blocked_reason to sim_history");
}

void verifyMembarUsesDynamicStreamSequence(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "2",
      {makeInstNode("VLDS", {"v0"}, {"memA"}),
       makeInstNode("VSTS", {"memB"}, {"v0"}),
       makeMembarNode("VST_VLD"),
       makeInstNode("VLDS", {"v1"}, {"memC"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_membar_dynamic";
  (void)runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  const int64_t firstPostBarrierLoadStart = cycleForInstId(starts, 2);
  const int64_t secondStoreDone = cycleForInstId(dones, 4);
  require(firstPostBarrierLoadStart < secondStoreDone,
          "native membar must use dynamic stream order, not static pc");
}

void verifyNativeLoadStoreDurationUsesOwnLatency() {
  ParamDB db = makeDurationTestDb();
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("VLDS", {"v0"}, {"memA"}),
       makeInstNode("VSTS", {"memB"}, {"v0"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_lsu_duration";
  (void)runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  require(cycleForInstId(dones, 0) - cycleForInstId(starts, 0) == 5,
          "native load duration must use load op latency");
  require(cycleForInstId(dones, 1) - cycleForInstId(starts, 1) == 7,
          "native store duration must use store op latency");
}

void verifyNativeNoImplicitUbStoreLoadDependency(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("VLDS", {"v0"}, {"memSrc"}),
       makeInstNode("VSTS", {"memA"}, {"v0"}),
       makeInstNode("VLDS", {"v1"}, {"memA"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_store_load_dep";
  (void)runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  require(cycleForInstId(starts, 2) < cycleForInstId(dones, 1),
          "native load without Membar must not wait for prior same-UB store");
}

void verifyUnsupportedMembarWarning(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeMembarNode("VV_ALL"),
       makeInstNode("VLDS", {"v0"}, {"memA"})})};
  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_bad_membar";
  (void)runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string warnings = readText(outDir / "model_warnings.json");
  require(warnings.find("unsupported_membar_type") != std::string::npos,
          "native unsupported membar must write warning");
}

void verifyNativeInputSymbolNormalization() {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "float32";
  ValueInfo mem;
  mem.valueId = "input";
  mem.storage = ValueStorageKind::UB;
  mem.dtype = "float32";
  ValueInfo fp;
  fp.valueId = "fp";
  fp.storage = ValueStorageKind::Register;
  fp.dtype = "f32";
  ValueInfo intValue;
  intValue.valueId = "ival";
  intValue.storage = ValueStorageKind::Register;
  intValue.dtype = "s32";
  ValueInfo packed;
  packed.valueId = "packed";
  packed.storage = ValueStorageKind::Register;
  packed.dtype = "bf16";
  ValueInfo packedOut;
  packedOut.valueId = "packedOut";
  packedOut.storage = ValueStorageKind::UB;
  packedOut.dtype = "bf16";
  ValueInfo out;
  out.valueId = "output";
  out.storage = ValueStorageKind::UB;
  out.dtype = "int32";
  vfInfo.values = {
      {"input", mem},
      {"fp", fp},
      {"ival", intValue},
      {"packed", packed},
      {"packedOut", packedOut},
      {"output", out},
  };
  ProgramNode vpackNode = makeInstNode("vpack", {"packed"}, {"fp"});
  vpackNode.inst.form = "b32";
  ProgramNode vsstbNode = makeInstNode("vsstb", {"packedOut"}, {"packed"});
  vsstbNode.inst.form = "b16";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("vld", {"fp"}, {"input"}),
       makeInstNode("vcvt", {"ival"}, {"fp"}),
       makeInstNode("vst", {"output"}, {"ival"}),
       vpackNode,
       vsstbNode,
       makeMembarNode("SMEM_BAR.VLD_VST")})};

  canonicalizeVfInfo(vfInfo);
  auto &body = vfInfo.body.front().loop->body;
  require(vfInfo.defaultDtype == "fp32", "native default dtype alias not normalized");
  require(vfInfo.values.at("fp").dtype == "fp32", "native value dtype alias not normalized");
  require(vfInfo.values.at("ival").dtype == "int32", "native int dtype alias not normalized");
  require(body[0].inst.op == "VLDS", "native vld alias not normalized");
  require(body[0].inst.form == "fp32", "native vld form alias not normalized");
  require(body[1].inst.op == "VCVT_F32_TO_S32", "native vcvt not specialized");
  require(body[1].inst.form == "f32_to_s32", "native vcvt form not normalized");
  require(body[2].inst.op == "VSTS", "native vst alias not normalized");
  require(body[3].inst.op == "VPACK", "native vpack alias not normalized");
  require(body[3].inst.form == "b32", "native vpack form not preserved");
  require(body[4].inst.op == "VSSTB", "native vsstb alias not normalized");
  require(body[4].inst.form == "b16", "native vsstb form not preserved");
  require(body[5].membar.barrier == "VLD_VST", "native membar alias not normalized");
}

void verifyNativeUnknownVcvtFallsBack(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  ValueInfo fp16;
  fp16.valueId = "half";
  fp16.storage = ValueStorageKind::Register;
  fp16.dtype = "fp16";
  ValueInfo intValue;
  intValue.valueId = "ival";
  intValue.storage = ValueStorageKind::Register;
  intValue.dtype = "int32";
  vfInfo.values = {{"half", fp16}, {"ival", intValue}};
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("vcvt", {"ival"}, {"half"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_unknown_vcvt";
  (void)runLegacyVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string warnings = readText(outDir / "model_warnings.json");
  require(warnings.find("unsupported_isa_op") != std::string::npos,
          "native unknown vcvt must fall back through ParamDB warning");
}

void verifySingleIterationLoopRunner(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.params = {{"I", 1}, {"U", 1}};
  vfInfo.body = {makeUnrolledLoopNode(
      "I", "U",
      {makeInstNode("VLDS", {"V1"}, {"memA"}),
       makeInstNode("VLDS", {"V2"}, {"memB"}),
       makeInstNode("VADD", {"V3"}, {"V1", "V2"}),
       makeInstNode("VSTS", {"memC"}, {"V3"})})};

  const auto result = runLegacyVfInfo(vfInfo, db, "", /*maxCycles=*/100000);
  require(result.cyclesExecuted == 43,
          "single-iteration canonicalized run cycles mismatch: " +
              std::to_string(result.cyclesExecuted));
  require(result.vfEndCycle == 55,
          "single-iteration canonicalized run vfEndCycle mismatch: " +
              std::to_string(result.vfEndCycle));
}

} // namespace

int main() {
  try {
    const std::filesystem::path root = std::filesystem::path(VFSIM_SOURCE_ROOT);
    ParamDB db(root);
    verifyInstructionFallback(db);
    verifySharedParamDbFallbackQueriesAreStable(db);
    verifyNativePartialCompatibleFormMerge();
    verifyVpackVsstbConfig(db);
    verifyVexpdifVmulscvtConfig(db);
    verifyBf16ConvertStoreConfig(db);
    verifyCompatibleFormFallback(db);
    verifyNativeVpackVsstbScheduling(db);
    verifyNativeExu0ReserveDispatch(db);
    verifyNativeThreePortsMode(db);
    verifyNativeUbSharedSlotsAndPregPressure(db);
    verifyNativeVectorAlignGenerations(db);
    verifyUnrollOrder(db);
    verifyMembarDisablesUnroll(db);
    verifyExplicitMembarTiming(db);
    verifyMembarUsesDynamicStreamSequence(db);
    verifyNativeLoadStoreDurationUsesOwnLatency();
    verifyNativeNoImplicitUbStoreLoadDependency(db);
    verifyUnsupportedMembarWarning(db);
    verifyNativeInputSymbolNormalization();
    verifyNativeUnknownVcvtFallsBack(db);
    verifySingleIterationLoopRunner(db);

    const auto program = buildTaddTmulProgram();
    ProgramAnalysis analysis;
    const auto loopBounds = analysis.inferTopBlockLoopBounds(program);
    ProgramFlatten flattener;
    const auto &linear = flattener.flatten(program);

    require(!linear.empty(), "flattened program must not be empty");

    const int topBlocks = countTopLevelLoops(program);
    IFU ifu(linear, {}, &db, loopBounds, topBlocks, "fp32");
    IDU idu(db.uarch(), db, {}, {}, topBlocks, loopBounds, "fp32");
    OoOCoreMainline ooo(db.uarch(), db, "fp32");

    const auto result =
        runSimulation(ifu, idu, ooo, db.uarch(), {}, "", /*maxCycles=*/5000);

    require(result.vfEndCycle > 0 && result.vfEndCycle < 200,
            "expected a short native VfSimulator run, got vfEndCycle=" +
                std::to_string(result.vfEndCycle));
    require(result.cyclesExecuted > 0, "cyclesExecuted must be positive");

    std::cout << "vfsim_native_smoke_test passed: vfEndCycle=" << result.vfEndCycle
              << '\n';
    return 0;
  } catch (const std::exception &ex) {
    std::cerr << "vfsim_native_smoke_test failed: " << ex.what() << '\n';
    return 1;
  }
}
