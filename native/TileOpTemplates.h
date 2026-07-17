// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#ifndef VFSIM_NATIVE_TILE_OP_TEMPLATES_H
#define VFSIM_NATIVE_TILE_OP_TEMPLATES_H

#include "native/ProgramAnalysis.h"

#include "mlir/IR/Value.h"
#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"

namespace mlir {
class Operation;
} // namespace mlir

namespace vfsim {

struct PlannedTileOpIR {
  mlir::Operation *op = nullptr;
  int64_t order = 0;
};

enum class UnrollLoopDimension {
  None,
  Row,
  Col,
};

struct LoweredTileGroupProgram {
  std::vector<ProgramNode> program;
  int64_t unrollTripCount = 0;
  UnrollLoopDimension unrollDimension = UnrollLoopDimension::None;
  std::string dtype = "fp32";
  std::string unsupportedReason;

  bool supported() const { return unsupportedReason.empty(); }
};

bool isSupportedElementwiseTileOp(llvm::StringRef opName);

LoweredTileGroupProgram
lowerTileGroupWithPerformanceTemplates(llvm::ArrayRef<PlannedTileOpIR> orderedOps);

} // namespace vfsim

#endif // VFSIM_NATIVE_TILE_OP_TEMPLATES_H
