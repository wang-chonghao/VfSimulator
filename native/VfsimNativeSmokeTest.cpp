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
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
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
        "load_done_latency": 99,
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

void verifyNativePartialCompatibleFormMerge() {
  ParamDB db = makePartialCompatTestDb();
  const InstConfig &partial = db.inst("VPARTIAL", "b16");
  require(partial.latency == 123, "native partial b16 latency override mismatch");
  require(partial.throughput == 2, "native partial b16 throughput must inherit fp16");
  require(partial.dataLoadCost == 9, "native partial b16 data_load_cost must inherit fp16");
  require(partial.dataStoreCost == 9, "native partial b16 data_store_cost must inherit fp16");
  require(partial.dispatchExu == "EXU01", "native partial b16 dispatch_exu must inherit fp16");
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
  const auto result = runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
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
  const auto result = runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
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
  (void)runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
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
  (void)runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  require(cycleForInstId(dones, 0) - cycleForInstId(starts, 0) == 5,
          "native load duration must use load op latency");
  require(cycleForInstId(dones, 1) - cycleForInstId(starts, 1) == 7,
          "native store duration must use store op latency");
}

void verifyNativeSameAddressStoreLoadDependency(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeInstNode("VLDS", {"v0"}, {"memSrc"}),
       makeInstNode("VSTS", {"memA"}, {"v0"}),
       makeInstNode("VLDS", {"v1"}, {"memA"})})};

  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_store_load_dep";
  (void)runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
  const std::string starts = readText(outDir / "start_by_cycle.json");
  const std::string dones = readText(outDir / "done_by_cycle.json");
  require(cycleForInstId(starts, 2) >= cycleForInstId(dones, 1),
          "native same-address load must wait for prior store done");
}

void verifyUnsupportedMembarWarning(const ParamDB &db) {
  VfInfo vfInfo;
  vfInfo.defaultDtype = "fp32";
  vfInfo.body = {makeLoopNode(
      "1",
      {makeMembarNode("VV_ALL"),
       makeInstNode("VLDS", {"v0"}, {"memA"})})};
  const auto outDir = std::filesystem::temp_directory_path() / "vfsim_native_bad_membar";
  (void)runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
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
  (void)runVfInfo(vfInfo, db, outDir.string(), /*maxCycles=*/100000);
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

  const auto result = runVfInfo(vfInfo, db, "", /*maxCycles=*/100000);
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
    verifyNativePartialCompatibleFormMerge();
    verifyVpackVsstbConfig(db);
    verifyCompatibleFormFallback(db);
    verifyNativeVpackVsstbScheduling(db);
    verifyUnrollOrder(db);
    verifyMembarDisablesUnroll(db);
    verifyExplicitMembarTiming(db);
    verifyMembarUsesDynamicStreamSequence(db);
    verifyNativeLoadStoreDurationUsesOwnLatency();
    verifyNativeSameAddressStoreLoadDependency(db);
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
