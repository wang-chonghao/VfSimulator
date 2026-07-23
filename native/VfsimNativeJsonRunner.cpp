// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "api/native/JsonVfInfoAdapter.h"
#include "native/ParamDB.h"
#include "native/SimulatorRunner.h"

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

using namespace vfsim;

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

    const VfInfo vfInfo = loadJsonVfInfo(args.tracePath);
    const auto result =
        runVfInfo(vfInfo, db, args.outDir.string(), args.maxCycles);

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
