// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/TileOpTemplates.h"

#include "mlir/IR/Operation.h"
#include "mlir/IR/Types.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringSwitch.h"
#include "llvm/Support/raw_ostream.h"

#include <optional>
#include <string>
#include <vector>

namespace vfsim {
namespace {

struct ElementwiseTemplate {
  llvm::StringLiteral microOp;
  unsigned tileInputs = 0;
  unsigned scalarInputs = 0;
  unsigned tileOutputs = 1;
  bool scalarAsVector = false;
};

struct TileShapeInfo {
  int64_t rows = 0;
  int64_t cols = 0;
  std::string dtype = "fp32";

  bool valid() const { return rows > 0 && cols > 0; }
};

static std::string stripPtoPrefix(llvm::StringRef opName) {
  if (opName.starts_with("pto."))
    return opName.drop_front(4).str();
  return opName.str();
}

static std::optional<ElementwiseTemplate>
lookupElementwiseTemplate(llvm::StringRef opName) {
  const std::string name = stripPtoPrefix(opName);
  return llvm::StringSwitch<std::optional<ElementwiseTemplate>>(name)
      .Case("tadd", ElementwiseTemplate{"VADD", 2, 0})
      .Case("tsub", ElementwiseTemplate{"VSUB", 2, 0})
      .Case("tmul", ElementwiseTemplate{"VMUL", 2, 0})
      .Case("tdiv", ElementwiseTemplate{"VDIV", 2, 0})
      .Case("tmax", ElementwiseTemplate{"VMAX", 2, 0})
      .Case("tmin", ElementwiseTemplate{"VMIN", 2, 0})
      .Case("tabs", ElementwiseTemplate{"VABS", 1, 0})
      .Case("texp", ElementwiseTemplate{"VEXP", 1, 0})
      .Case("tadds", ElementwiseTemplate{"VADDS", 1, 1})
      .Case("tsubs", ElementwiseTemplate{"VSUB", 1, 1, 1, true})
      .Case("tmuls", ElementwiseTemplate{"VMULS", 1, 1})
      .Case("tdivs", ElementwiseTemplate{"VDIV", 1, 1, 1, true})
      .Case("tmaxs", ElementwiseTemplate{"VMAXS", 1, 1})
      .Case("tmins", ElementwiseTemplate{"VMINS", 1, 1})
      .Case("tcvt", ElementwiseTemplate{"VCVT_F32_TO_F16", 1, 0})
      .Default(std::nullopt);
}

static ProgramNode makeInstNode(std::string op, std::vector<std::string> dst,
                                std::vector<std::string> src) {
  ProgramInstNode inst;
  inst.op = std::move(op);
  inst.dst = std::move(dst);
  inst.src = std::move(src);
  return ProgramNode::makeInst(std::move(inst));
}

static bool isTileLike(mlir::Value value) {
  if (!value)
    return false;
  std::string typeText;
  llvm::raw_string_ostream os(typeText);
  value.getType().print(os);
  return os.str().find("tile_buf") != std::string::npos;
}

static std::string typeToString(mlir::Type type) {
  std::string typeText;
  llvm::raw_string_ostream os(typeText);
  type.print(os);
  return os.str();
}

static std::optional<TileShapeInfo> parseStaticTileShape(mlir::Type type) {
  const std::string text = typeToString(type);
  const std::size_t xPos = text.find('x');
  const std::size_t dtypeSep =
      text.find('x', xPos == std::string::npos ? 0 : xPos + 1);
  if (xPos == std::string::npos || dtypeSep == std::string::npos ||
      dtypeSep <= xPos + 1)
    return std::nullopt;

  std::size_t firstStart = xPos;
  while (firstStart > 0 &&
         std::isdigit(static_cast<unsigned char>(text[firstStart - 1])))
    --firstStart;
  if (firstStart == xPos)
    return std::nullopt;

  const std::string rowsText = text.substr(firstStart, xPos - firstStart);
  const std::string colsText = text.substr(xPos + 1, dtypeSep - (xPos + 1));
  if (rowsText.empty() || colsText.empty())
    return std::nullopt;
  if (!llvm::all_of(rowsText, [](char c) {
        return std::isdigit(static_cast<unsigned char>(c));
      }) ||
      !llvm::all_of(colsText, [](char c) {
        return std::isdigit(static_cast<unsigned char>(c));
      }))
    return std::nullopt;

  const int64_t rows = std::stoll(rowsText);
  const int64_t cols = std::stoll(colsText);
  if (rows <= 0 || cols <= 0)
    return std::nullopt;
  TileShapeInfo info;
  info.rows = rows;
  info.cols = cols;
  if (text.find("xf16") != std::string::npos ||
      text.find("xbf16") != std::string::npos) {
    info.dtype = "fp16";
  } else {
    info.dtype = "fp32";
  }
  return info;
}

static int64_t lanesForDType(const std::string &dtype) {
  if (dtype == "fp16")
    return 128;
  return 64;
}

static std::string nextVreg(unsigned &nextId) {
  return "v" + std::to_string(nextId++);
}

static std::string nextMem(unsigned &nextId) {
  return "mem" + std::to_string(nextId++);
}

static std::string materializeInput(
    mlir::Value value, std::vector<ProgramNode> &body,
    llvm::DenseMap<mlir::Value, std::string> &valueToVreg, unsigned &nextVregId,
    unsigned &nextMemId) {
  auto existing = valueToVreg.find(value);
  if (existing != valueToVreg.end())
    return existing->second;

  const std::string reg = nextVreg(nextVregId);
  body.push_back(makeInstNode("VLDS", {reg}, {nextMem(nextMemId)}));
  valueToVreg.try_emplace(value, reg);
  return reg;
}

static void materializeExternalStores(
    llvm::ArrayRef<mlir::Value> producedValues,
    const llvm::DenseMap<mlir::Value, unsigned> &internalUseCount,
    const llvm::DenseMap<mlir::Value, std::string> &valueToVreg,
    std::vector<ProgramNode> &body, unsigned &nextMemId) {
  for (mlir::Value value : producedValues) {
    auto useIt = internalUseCount.find(value);
    if (useIt != internalUseCount.end() && useIt->second > 0)
      continue;
    auto regIt = valueToVreg.find(value);
    if (regIt == valueToVreg.end())
      continue;
    body.push_back(makeInstNode("VSTS", {nextMem(nextMemId)}, {regIt->second}));
  }
}

static ProgramNode makeLoopNode(std::string name, int64_t iters,
                                std::string unroll,
                                std::vector<ProgramNode> body) {
  ProgramLoopNode loop;
  loop.name = std::move(name);
  loop.iters = std::to_string(iters);
  loop.unroll = std::move(unroll);
  loop.body = std::move(body);
  return ProgramNode::makeLoop(std::move(loop));
}

} // namespace

bool isSupportedElementwiseTileOp(llvm::StringRef opName) {
  return lookupElementwiseTemplate(opName).has_value();
}

LoweredTileGroupProgram
lowerTileGroupWithPerformanceTemplates(llvm::ArrayRef<PlannedTileOpIR> orderedOps) {
  LoweredTileGroupProgram lowered;
  if (orderedOps.empty()) {
    lowered.unsupportedReason = "empty fusion group";
    return lowered;
  }

  llvm::DenseMap<mlir::Value, std::string> valueToVreg;
  llvm::DenseMap<mlir::Value, unsigned> internalUseCount;
  llvm::SmallVector<mlir::Value, 8> producedValues;
  TileShapeInfo loopShape;
  unsigned nextVregId = 0;
  unsigned nextMemId = 0;
  std::vector<ProgramNode> innerBody;

  for (const PlannedTileOpIR &tileOp : orderedOps) {
    const auto templ =
        lookupElementwiseTemplate(tileOp.op->getName().getStringRef());
    if (!templ) {
      lowered.unsupportedReason =
          "unsupported tile op: " + tileOp.op->getName().getStringRef().str();
      return lowered;
    }

    const unsigned requiredOperands =
        templ->tileInputs + templ->scalarInputs + templ->tileOutputs;
    if (tileOp.op->getNumOperands() < requiredOperands) {
      lowered.unsupportedReason =
          "too few operands for tile op: " +
          tileOp.op->getName().getStringRef().str();
      return lowered;
    }

    const unsigned outputBase =
        tileOp.op->getNumOperands() - templ->tileOutputs;
    for (unsigned i = 0; i < templ->tileInputs; ++i) {
      mlir::Value operand = tileOp.op->getOperand(i);
      if (valueToVreg.find(operand) != valueToVreg.end())
        ++internalUseCount[operand];
    }

    std::vector<std::string> srcRegs;
    for (unsigned i = 0; i < templ->tileInputs; ++i) {
      mlir::Value operand = tileOp.op->getOperand(i);
      if (!isTileLike(operand)) {
        lowered.unsupportedReason =
            "expected tile input for tile op: " +
            tileOp.op->getName().getStringRef().str();
        return lowered;
      }
      srcRegs.push_back(materializeInput(operand, innerBody, valueToVreg,
                                         nextVregId, nextMemId));
    }

    if (templ->scalarInputs > 0) {
      if (!templ->scalarAsVector) {
        srcRegs.push_back("scalar");
      } else {
        const std::string scalarReg = nextVreg(nextVregId);
        innerBody.push_back(makeInstNode("VBR", {scalarReg}, {"scalar"}));
        srcRegs.push_back(scalarReg);
      }
    }

    mlir::Value dstValue = tileOp.op->getOperand(outputBase);
    if (!isTileLike(dstValue)) {
      lowered.unsupportedReason =
          "expected tile output for tile op: " +
          tileOp.op->getName().getStringRef().str();
      return lowered;
    }

    if (!loopShape.valid()) {
      auto shape = parseStaticTileShape(dstValue.getType());
      if (!shape) {
        lowered.unsupportedReason =
            "cannot infer static loop shape for tile op: " +
            tileOp.op->getName().getStringRef().str();
        return lowered;
      }
      loopShape = *shape;
      lowered.dtype = loopShape.dtype;
      const int64_t lanes = lanesForDType(lowered.dtype);
      const int64_t colTripCount = (loopShape.cols + lanes - 1) / lanes;
      if (colTripCount == 1) {
        lowered.unrollTripCount = loopShape.rows;
        lowered.unrollDimension = UnrollLoopDimension::Row;
      } else {
        lowered.unrollTripCount = colTripCount;
        lowered.unrollDimension = UnrollLoopDimension::Col;
      }
    }

    const std::string dstReg = nextVreg(nextVregId);
    innerBody.push_back(makeInstNode(templ->microOp.str(), {dstReg}, srcRegs));
    valueToVreg[dstValue] = dstReg;
    producedValues.push_back(dstValue);
  }

  materializeExternalStores(producedValues, internalUseCount, valueToVreg,
                            innerBody, nextMemId);
  if (!loopShape.valid() || lowered.unrollTripCount <= 0) {
    lowered.unsupportedReason = "cannot construct loop structure";
    return lowered;
  }

  const int64_t lanes = lanesForDType(lowered.dtype);
  const int64_t colTripCount = (loopShape.cols + lanes - 1) / lanes;
  if (colTripCount == 1) {
    lowered.program.push_back(makeLoopNode("tile_flattened_row_loop",
                                           loopShape.rows,
                                           "vfsim_inner_unroll",
                                           std::move(innerBody)));
    return lowered;
  }

  std::vector<ProgramNode> rowBody;
  rowBody.push_back(makeLoopNode("tile_col_loop", colTripCount,
                                 "vfsim_inner_unroll", std::move(innerBody)));
  lowered.program.push_back(
      makeLoopNode("tile_row_loop", loopShape.rows, "1", std::move(rowBody)));
  return lowered;
}

} // namespace vfsim
