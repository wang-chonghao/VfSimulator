// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/IDU.h"
#include "native/IFU.h"
#include "native/Json.h"
#include "native/OOO.h"
#include "native/ParamDB.h"
#include "native/ProgramAnalysis.h"
#include "native/ProgramCanonicalization.h"
#include "native/ProgramFlatten.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace vfsim;

const json::Value *findKey(const json::Value::Object &obj, const std::string &key) {
  auto it = obj.find(key);
  return it == obj.end() ? nullptr : &it->second;
}

std::vector<std::string> parseStringArray(const json::Value &value, const std::string &field) {
  if (!value.isArray())
    throw std::runtime_error(field + " must be an array");
  std::vector<std::string> out;
  for (const auto &item : value.asArray())
    out.push_back(item.asString());
  return out;
}

std::vector<ProgramNode> parseProgramArray(const json::Value &value);

ProgramNode parseProgramNode(const json::Value &value) {
  if (!value.isObject())
    throw std::runtime_error("program node must be an object");
  const auto &obj = value.asObject();
  const auto *typeValue = findKey(obj, "type");
  const std::string type = typeValue ? typeValue->asString() : "";

  if (type == "inst") {
    ProgramInstNode inst;
    const auto *opValue = findKey(obj, "op");
    if (!opValue)
      throw std::runtime_error("inst node missing op");
    inst.op = opValue->asString();
    if (const auto *dstValue = findKey(obj, "dst"))
      inst.dst = parseStringArray(*dstValue, "dst");
    if (const auto *srcValue = findKey(obj, "src"))
      inst.src = parseStringArray(*srcValue, "src");
    return ProgramNode::makeInst(std::move(inst));
  }

  if (type == "loop") {
    ProgramLoopNode loop;
    const auto *itersValue = findKey(obj, "iters");
    if (!itersValue)
      throw std::runtime_error("loop node missing iters");
    loop.iters = itersValue->isString() ? itersValue->asString() : std::to_string(itersValue->asInt());
    if (const auto *unrollValue = findKey(obj, "unroll"))
      loop.unroll = unrollValue->isString() ? unrollValue->asString() : std::to_string(unrollValue->asInt());
    if (const auto *nameValue = findKey(obj, "name"))
      loop.name = nameValue->asString();
    const auto *bodyValue = findKey(obj, "body");
    if (!bodyValue)
      throw std::runtime_error("loop node missing body");
    loop.body = parseProgramArray(*bodyValue);
    return ProgramNode::makeLoop(std::move(loop));
  }

  throw std::runtime_error("unsupported program node type: " + type);
}

std::vector<ProgramNode> parseProgramArray(const json::Value &value) {
  if (!value.isArray())
    throw std::runtime_error("program must be an array");
  std::vector<ProgramNode> out;
  for (const auto &node : value.asArray())
    out.push_back(parseProgramNode(node));
  return out;
}

ProgramAnalysis::ParamMap parseParams(const json::Value::Object &obj) {
  ProgramAnalysis::ParamMap params;
  const auto *paramsValue = findKey(obj, "params");
  if (!paramsValue || paramsValue->isNull())
    return params;
  for (const auto &[key, value] : paramsValue->asObject())
    params[key] = value.asInt();
  return params;
}

int countTopLevelLoops(const std::vector<ProgramNode> &program) {
  int count = 0;
  for (const auto &node : program) {
    if (node.kind == ProgramNode::Kind::Loop)
      ++count;
  }
  return count;
}

struct Args {
  std::filesystem::path tracePath;
  std::filesystem::path outDir;
  int64_t maxCycles = 1000000;
};

Args parseArgs(int argc, char **argv) {
  Args args;
  for (int i = 1; i < argc; ++i) {
    const std::string cur = argv[i];
    if (cur == "--trace" && i + 1 < argc) {
      args.tracePath = argv[++i];
      continue;
    }
    if (cur == "--out-dir" && i + 1 < argc) {
      args.outDir = argv[++i];
      continue;
    }
    if (cur == "--max-cycles" && i + 1 < argc) {
      args.maxCycles = std::stoll(argv[++i]);
      continue;
    }
    throw std::runtime_error("unknown or incomplete argument: " + cur);
  }
  if (args.tracePath.empty())
    throw std::runtime_error("missing --trace");
  return args;
}

} // namespace

int main(int argc, char **argv) {
  try {
    const Args args = parseArgs(argc, argv);
    const std::filesystem::path root = std::filesystem::path(VFSIM_SOURCE_ROOT);
    ParamDB db(root);

    const auto trace = json::parseFile(args.tracePath);
    const auto &traceObj = trace.asObject();
    const auto *programValue = findKey(traceObj, "program");
    if (!programValue)
      throw std::runtime_error("trace missing program");
    const std::string dtype = findKey(traceObj, "dtype") ? findKey(traceObj, "dtype")->asString("fp32") : "fp32";
    const auto params = parseParams(traceObj);
    const auto inputProgram = parseProgramArray(*programValue);
    const auto program = canonicalizeSingleSuperIterationLoops(
        inputProgram, params, db, dtype);

    ProgramAnalysis analysis(params);
    const auto loopBounds = analysis.inferTopBlockLoopBounds(program);
    ProgramFlatten flattener(params);
    const auto &linear = flattener.flatten(program);
    const int topBlocks = countTopLevelLoops(program);

    IFU ifu(linear, params, &db, loopBounds, topBlocks, dtype);
    IDU idu(db.uarch(), db, params, {}, topBlocks, loopBounds, dtype);
    OoOCoreMainline ooo(db.uarch(), db, dtype);

    const auto result = runSimulation(
        ifu, idu, ooo, db.uarch(), params, args.outDir.string(), args.maxCycles);

    std::cout << "cyclesExecuted=" << result.cyclesExecuted << "\n";
    std::cout << "vfEndCycle=" << result.vfEndCycle << "\n";
    if (!result.resultsDir.empty())
      std::cout << "resultsDir=" << result.resultsDir << "\n";
    return 0;
  } catch (const std::exception &ex) {
    std::cerr << "vfsim_native_json_runner failed: " << ex.what() << '\n';
    return 1;
  }
}
