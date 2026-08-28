// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS
// FOR A PARTICULAR PURPOSE. See LICENSE in the root of the software repository
// for the full text of the License.

#include "native/TileOpTemplates.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Types.h"
#include "mlir/IR/Value.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringSwitch.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cctype>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vfsim {
namespace {

constexpr llvm::StringLiteral kCandidatesAttr = "candidates";
constexpr llvm::StringLiteral kCandidateName = "name";
constexpr llvm::StringLiteral kCandidateLoopDepth = "loop_depth";

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

struct SelectedTemplateInfo {
  std::string name;
  int64_t loopDepth = 0;
};

std::string stripPtoPrefix(llvm::StringRef opName) {
  if (opName.starts_with("pto."))
    return opName.drop_front(4).str();
  return opName.str();
}

std::optional<ElementwiseTemplate>
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

std::optional<SelectedTemplateInfo>
getSelectedTemplate(mlir::Operation *op, std::string &failureReason) {
  auto candidates = op->getAttrOfType<mlir::ArrayAttr>(kCandidatesAttr);
  if (!candidates || candidates.empty()) {
    failureReason = "missing selected TileLib candidate on " +
                    op->getName().getStringRef().str();
    return std::nullopt;
  }
  auto selected = mlir::dyn_cast<mlir::DictionaryAttr>(candidates[0]);
  if (!selected) {
    failureReason = "malformed selected TileLib candidate on " +
                    op->getName().getStringRef().str();
    return std::nullopt;
  }
  auto name = selected.getAs<mlir::StringAttr>(kCandidateName);
  auto loopDepth = selected.getAs<mlir::IntegerAttr>(kCandidateLoopDepth);
  if (!name || !loopDepth ||
      (loopDepth.getInt() != 1 && loopDepth.getInt() != 2)) {
    failureReason = "selected TileLib candidate requires name and loop_depth "
                    "1 or 2 on " +
                    op->getName().getStringRef().str();
    return std::nullopt;
  }
  return SelectedTemplateInfo{name.getValue().str(), loopDepth.getInt()};
}

bool isTileLike(mlir::Value value) {
  if (!value)
    return false;
  std::string typeText;
  llvm::raw_string_ostream os(typeText);
  value.getType().print(os);
  return os.str().find("tile_buf") != std::string::npos;
}

std::string typeToString(mlir::Type type) {
  std::string typeText;
  llvm::raw_string_ostream os(typeText);
  type.print(os);
  return os.str();
}

std::optional<TileShapeInfo> parseStaticTileShape(mlir::Type type) {
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
  const std::string colsText = text.substr(xPos + 1, dtypeSep - xPos - 1);
  if (rowsText.empty() || colsText.empty() ||
      !llvm::all_of(
          rowsText,
          [](char c) { return std::isdigit(static_cast<unsigned char>(c)); }) ||
      !llvm::all_of(colsText, [](char c) {
        return std::isdigit(static_cast<unsigned char>(c));
      }))
    return std::nullopt;

  TileShapeInfo info;
  info.rows = std::stoll(rowsText);
  info.cols = std::stoll(colsText);
  if (info.rows <= 0 || info.cols <= 0)
    return std::nullopt;
  if (text.find("xf16") != std::string::npos ||
      text.find("xbf16") != std::string::npos)
    info.dtype = "fp16";
  return info;
}

int64_t lanesForDType(llvm::StringRef dtype) {
  return dtype == "fp16" ? 128 : 64;
}

std::string compactDType(llvm::StringRef dtype) {
  return llvm::StringSwitch<std::string>(dtype)
      .Case("fp32", "f32")
      .Case("fp16", "f16")
      .Case("int32", "s32")
      .Case("uint32", "u32")
      .Default(dtype.str());
}

std::string conversionForm(llvm::StringRef srcDtype, llvm::StringRef dstDtype) {
  return compactDType(srcDtype) + "_to_" + compactDType(dstDtype);
}

std::optional<std::string> dtypeOf(mlir::Value value) {
  if (auto shape = parseStaticTileShape(value.getType()))
    return shape->dtype;
  return std::nullopt;
}

CanonicalOperand makeValueOperand(llvm::StringRef valueId,
                                  CanonicalOperandRole role,
                                  llvm::StringRef dtype) {
  CanonicalOperand operand;
  operand.valueId = valueId.str();
  operand.role = role;
  operand.dtype = dtype.str();
  return operand;
}

class CanonicalTileBuilder {
public:
  CanonicalTileBuilder(TileShapeInfo shape, TileTraversal traversal)
      : shape_(std::move(shape)), traversal_(traversal),
        lanes_(lanesForDType(shape_.dtype)) {}

  CanonicalVfInfo takeVfInfo(std::vector<CanonicalNode> body) {
    if (traversal_ == TileTraversal::OneDimensional) {
      CanonicalLoop loop;
      loop.loopId = "tile_flat_loop";
      loop.induction.variableId = "tile_flat_iv";
      loop.count = (shape_.rows * shape_.cols + lanes_ - 1) / lanes_;
      loop.body = std::move(body);
      vfInfo_.context.push_back(CanonicalNode::makeLoop(std::move(loop)));
      return std::move(vfInfo_);
    }

    CanonicalLoop colLoop;
    colLoop.loopId = "tile_col_loop";
    colLoop.induction.variableId = "tile_col_iv";
    colLoop.count = (shape_.cols + lanes_ - 1) / lanes_;
    colLoop.body = std::move(body);

    CanonicalLoop rowLoop;
    rowLoop.loopId = "tile_row_loop";
    rowLoop.induction.variableId = "tile_row_iv";
    rowLoop.count = shape_.rows;
    rowLoop.body.push_back(CanonicalNode::makeLoop(std::move(colLoop)));
    vfInfo_.context.push_back(CanonicalNode::makeLoop(std::move(rowLoop)));
    return std::move(vfInfo_);
  }

  std::string materializeTileInput(
      mlir::Value value, std::vector<CanonicalNode> &body,
      llvm::DenseMap<mlir::Value, std::string> &valueToRegister) {
    auto existing = valueToRegister.find(value);
    if (existing != valueToRegister.end())
      return existing->second;

    const std::string ubValue = getOrCreateExternalUbValue(value);
    const std::string instructionId = nextInstructionId("vlds");
    const std::string registerId = nextValueId("reg");
    const std::string dtype = dtypeOf(value).value_or(shape_.dtype);
    registerValue(registerId, registerId, CanonicalStorageKind::Register, dtype,
                  {lanesForDType(dtype)}, instructionId, std::nullopt);

    CanonicalInstruction instruction;
    instruction.instructionId = instructionId;
    instruction.opcode = "VLDS";
    instruction.instructionClass = CanonicalInstructionClass::Load;
    instruction.form = dtype;
    instruction.inputs.push_back(
        makeMemoryOperand(ubValue, CanonicalAccessKind::Read, addressTerms()));
    instruction.outputs.push_back(
        makeValueOperand(registerId, CanonicalOperandRole::Destination, dtype));
    body.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
    valueToRegister.try_emplace(value, registerId);
    return registerId;
  }

  std::string materializeScalar(mlir::Value value, llvm::StringRef dtype) {
    auto existing = scalarValues_.find(value);
    if (existing != scalarValues_.end())
      return existing->second;
    const std::string scalarId = nextValueId("scalar");
    registerValue(scalarId, scalarId, CanonicalStorageKind::Scalar, dtype.str(),
                  {}, std::nullopt, std::nullopt);
    scalarValues_.try_emplace(value, scalarId);
    return scalarId;
  }

  std::string appendCompute(llvm::StringRef opcode, llvm::StringRef form,
                            llvm::StringRef outputDtype,
                            llvm::ArrayRef<std::string> inputs,
                            std::vector<CanonicalNode> &body) {
    const std::string instructionId = nextInstructionId("compute");
    const std::string registerId = nextValueId("reg");
    registerValue(registerId, registerId, CanonicalStorageKind::Register,
                  outputDtype.str(), {lanesForDType(outputDtype)},
                  instructionId, std::nullopt);

    CanonicalInstruction instruction;
    instruction.instructionId = instructionId;
    instruction.opcode = opcode.str();
    instruction.instructionClass = CanonicalInstructionClass::Compute;
    instruction.form = form.str();
    for (const std::string &input : inputs) {
      const CanonicalValue &value = vfInfo_.values.at(input);
      const CanonicalOperandRole role =
          value.storage == CanonicalStorageKind::Scalar
              ? CanonicalOperandRole::Scalar
              : CanonicalOperandRole::Source;
      instruction.inputs.push_back(makeValueOperand(input, role, value.dtype));
    }
    instruction.outputs.push_back(makeValueOperand(
        registerId, CanonicalOperandRole::Destination, outputDtype));
    body.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
    return registerId;
  }

  void appendStore(mlir::Value value, llvm::StringRef registerId,
                   std::vector<CanonicalNode> &body) {
    const std::string instructionId = nextInstructionId("vsts");
    const std::string dtype = dtypeOf(value).value_or(shape_.dtype);
    const std::string storageObject = getOrCreateStorageObject(value);
    const std::string writtenValue = nextValueId("ub_write");
    registerValue(writtenValue, storageObject, CanonicalStorageKind::UB, dtype,
                  {shape_.rows, shape_.cols}, instructionId, storageObject);

    CanonicalInstruction instruction;
    instruction.instructionId = instructionId;
    instruction.opcode = "VSTS";
    instruction.instructionClass = CanonicalInstructionClass::Store;
    instruction.form = dtype;
    instruction.inputs.push_back(
        makeValueOperand(registerId, CanonicalOperandRole::Source,
                         vfInfo_.values.at(registerId.str()).dtype));
    instruction.outputs.push_back(makeMemoryOperand(
        writtenValue, CanonicalAccessKind::Write, addressTerms()));
    body.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
  }

private:
  TileShapeInfo shape_;
  TileTraversal traversal_;
  int64_t lanes_ = 0;
  CanonicalVfInfo vfInfo_;
  unsigned nextInstruction_ = 0;
  unsigned nextValue_ = 0;
  unsigned nextStorage_ = 0;
  llvm::DenseMap<mlir::Value, std::string> storageObjects_;
  llvm::DenseMap<mlir::Value, std::string> externalUbValues_;
  llvm::DenseMap<mlir::Value, std::string> scalarValues_;

  std::string nextInstructionId(llvm::StringRef kind) {
    return "inst." + kind.str() + "." + std::to_string(nextInstruction_++);
  }

  std::string nextValueId(llvm::StringRef kind) {
    return "value." + kind.str() + "." + std::to_string(nextValue_++);
  }

  void registerValue(llvm::StringRef definitionId, llvm::StringRef logicalId,
                     CanonicalStorageKind storage, std::string dtype,
                     std::vector<int64_t> shape,
                     std::optional<std::string> producer,
                     std::optional<std::string> storageObject) {
    CanonicalValue value;
    value.definitionId = definitionId.str();
    value.logicalId = logicalId.str();
    value.storage = storage;
    value.dtype = std::move(dtype);
    value.shape = std::move(shape);
    value.producerNodeId = std::move(producer);
    value.storageObjectId = std::move(storageObject);
    vfInfo_.values.emplace(value.definitionId, std::move(value));
  }

  std::string getOrCreateStorageObject(mlir::Value value) {
    auto existing = storageObjects_.find(value);
    if (existing != storageObjects_.end())
      return existing->second;
    const std::string objectId = "ub." + std::to_string(nextStorage_++);
    CanonicalStorageObject object;
    object.objectId = objectId;
    object.storage = CanonicalStorageKind::UB;
    object.shape = {shape_.rows, shape_.cols};
    vfInfo_.storageObjects.emplace(objectId, std::move(object));
    storageObjects_.try_emplace(value, objectId);
    return objectId;
  }

  std::string getOrCreateExternalUbValue(mlir::Value value) {
    auto existing = externalUbValues_.find(value);
    if (existing != externalUbValues_.end())
      return existing->second;
    const std::string objectId = getOrCreateStorageObject(value);
    const std::string valueId = nextValueId("ub");
    const std::string dtype = dtypeOf(value).value_or(shape_.dtype);
    registerValue(valueId, objectId, CanonicalStorageKind::UB, dtype,
                  {shape_.rows, shape_.cols}, std::nullopt, objectId);
    externalUbValues_.try_emplace(value, valueId);
    return valueId;
  }

  std::vector<CanonicalAffineTerm> addressTerms() const {
    if (traversal_ == TileTraversal::OneDimensional)
      return {{"tile_flat_iv", lanes_}};
    return {{"tile_row_iv", shape_.cols}, {"tile_col_iv", lanes_}};
  }

  CanonicalOperand
  makeMemoryOperand(llvm::StringRef valueId, CanonicalAccessKind access,
                    std::vector<CanonicalAffineTerm> terms) const {
    CanonicalOperand operand =
        makeValueOperand(valueId, CanonicalOperandRole::Memory,
                         vfInfo_.values.at(valueId.str()).dtype);
    CanonicalMemoryAccess memory;
    memory.baseObjectId = *vfInfo_.values.at(valueId.str()).storageObjectId;
    memory.offset.terms = std::move(terms);
    memory.accessKind = access;
    memory.span = lanes_;
    operand.memoryAccess = std::move(memory);
    return operand;
  }
};

int64_t integerExpression(const CanonicalIntegerExpression &expression,
                          llvm::StringRef field) {
  if (const auto *value = std::get_if<int64_t>(&expression))
    return *value;
  throw std::runtime_error("symbolic " + field.str() +
                           " is unsupported in TileOp candidate expansion");
}

std::vector<CanonicalNode>
expandInstructionBody(CanonicalVfInfo &vfInfo, const CanonicalLoop &loop,
                      unsigned factor, TileUnrollOrder order, bool removeLoop) {
  std::vector<CanonicalInstruction> instructions;
  instructions.reserve(loop.body.size());
  for (const CanonicalNode &node : loop.body) {
    const auto *instruction = std::get_if<CanonicalInstruction>(&node.payload);
    if (!instruction)
      throw std::runtime_error(
          "TileOp unroll target must have an instruction-only body");
    instructions.push_back(*instruction);
  }

  std::vector<std::unordered_map<std::string, std::string>> valueMaps(factor);
  std::vector<std::unordered_map<std::string, std::string>> instructionMaps(
      factor);
  std::vector<std::string> originalOutputs;
  for (unsigned lane = 0; lane < factor; ++lane) {
    for (const CanonicalInstruction &instruction : instructions) {
      const std::string clonedInstruction =
          instruction.instructionId + ".u" + std::to_string(lane);
      instructionMaps[lane].emplace(instruction.instructionId,
                                    clonedInstruction);
      for (const CanonicalOperand &output : instruction.outputs) {
        auto value = vfInfo.values.find(output.valueId);
        if (value == vfInfo.values.end())
          throw std::runtime_error("TileOp candidate output has no value: " +
                                   output.valueId);
        const std::string clonedValue =
            output.valueId + ".u" + std::to_string(lane);
        valueMaps[lane].emplace(output.valueId, clonedValue);
        CanonicalValue cloned = value->second;
        cloned.definitionId = clonedValue;
        cloned.producerNodeId = clonedInstruction;
        vfInfo.values.emplace(clonedValue, std::move(cloned));
        if (lane == 0)
          originalOutputs.push_back(output.valueId);
      }
    }
  }

  const int64_t originalStep = integerExpression(loop.induction.step, "step");
  auto cloneInstruction = [&](const CanonicalInstruction &instruction,
                              unsigned lane) {
    CanonicalInstruction cloned = instruction;
    cloned.instructionId = instructionMaps[lane].at(instruction.instructionId);
    auto remapOperand = [&](CanonicalOperand &operand) {
      auto mapped = valueMaps[lane].find(operand.valueId);
      if (mapped != valueMaps[lane].end())
        operand.valueId = mapped->second;
      if (!operand.memoryAccess)
        return;
      auto &offset = operand.memoryAccess->offset;
      std::vector<CanonicalAffineTerm> rewrittenTerms;
      for (const CanonicalAffineTerm &term : offset.terms) {
        if (term.variableId != loop.induction.variableId) {
          rewrittenTerms.push_back(term);
          continue;
        }
        offset.constant +=
            static_cast<int64_t>(lane) * originalStep * term.coefficient;
        if (!removeLoop)
          rewrittenTerms.push_back(term);
      }
      offset.terms = std::move(rewrittenTerms);
    };
    for (CanonicalOperand &input : cloned.inputs)
      remapOperand(input);
    for (CanonicalOperand &output : cloned.outputs)
      remapOperand(output);
    return CanonicalNode::makeInstruction(std::move(cloned));
  };

  std::vector<CanonicalNode> expanded;
  expanded.reserve(instructions.size() * factor);
  if (order == TileUnrollOrder::Abcabc) {
    for (unsigned lane = 0; lane < factor; ++lane)
      for (const CanonicalInstruction &instruction : instructions)
        expanded.push_back(cloneInstruction(instruction, lane));
  } else {
    for (const CanonicalInstruction &instruction : instructions)
      for (unsigned lane = 0; lane < factor; ++lane)
        expanded.push_back(cloneInstruction(instruction, lane));
  }

  for (const std::string &output : originalOutputs)
    vfInfo.values.erase(output);
  return expanded;
}

std::vector<CanonicalNode>
rewriteUnrollTarget(CanonicalVfInfo &vfInfo,
                    const std::vector<CanonicalNode> &nodes,
                    llvm::StringRef targetLoopId, int64_t targetTripCount,
                    unsigned factor, TileUnrollOrder order, bool &found) {
  std::vector<CanonicalNode> rewritten;
  for (const CanonicalNode &node : nodes) {
    const auto *loopPtr =
        std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
    if (!loopPtr || !*loopPtr) {
      rewritten.push_back(node);
      continue;
    }

    const CanonicalLoop &loop = **loopPtr;
    if (loop.loopId == targetLoopId) {
      if (targetTripCount <= 0 || targetTripCount % factor != 0)
        throw std::runtime_error(
            "invalid TileOp unroll factor for modeled loop");
      found = true;
      const int64_t remaining = targetTripCount / factor;
      std::vector<CanonicalNode> expanded =
          expandInstructionBody(vfInfo, loop, factor, order, remaining == 1);
      if (remaining == 1) {
        rewritten.insert(rewritten.end(),
                         std::make_move_iterator(expanded.begin()),
                         std::make_move_iterator(expanded.end()));
        continue;
      }
      CanonicalLoop candidateLoop = loop;
      candidateLoop.count = remaining;
      candidateLoop.unroll = int64_t{1};
      candidateLoop.induction.step =
          integerExpression(loop.induction.step, "step") *
          static_cast<int64_t>(factor);
      candidateLoop.body = std::move(expanded);
      rewritten.push_back(CanonicalNode::makeLoop(std::move(candidateLoop)));
      continue;
    }

    CanonicalLoop outer = loop;
    outer.body = rewriteUnrollTarget(vfInfo, loop.body, targetLoopId,
                                     targetTripCount, factor, order, found);
    rewritten.push_back(CanonicalNode::makeLoop(std::move(outer)));
  }
  return rewritten;
}

} // namespace

bool isSupportedElementwiseTileOp(llvm::StringRef opName) {
  return lookupElementwiseTemplate(opName).has_value();
}

LoweredTileGroupProgram
lowerTileGroupToCanonicalVfInfo(llvm::ArrayRef<PlannedTileOpIR> orderedOps) {
  LoweredTileGroupProgram lowered;
  if (orderedOps.empty()) {
    lowered.unsupportedReason = "empty fusion group";
    return lowered;
  }

  std::optional<TileShapeInfo> loopShape;
  std::optional<int64_t> selectedLoopDepth;
  std::vector<ElementwiseTemplate> templates;
  templates.reserve(orderedOps.size());
  for (const PlannedTileOpIR &tileOp : orderedOps) {
    const auto elementwise =
        lookupElementwiseTemplate(tileOp.op->getName().getStringRef());
    if (!elementwise) {
      lowered.unsupportedReason =
          "unsupported tile op: " + tileOp.op->getName().getStringRef().str();
      return lowered;
    }
    templates.push_back(*elementwise);

    std::string selectionFailure;
    auto selected = getSelectedTemplate(tileOp.op, selectionFailure);
    if (!selected) {
      lowered.unsupportedReason = std::move(selectionFailure);
      return lowered;
    }
    if (!selectedLoopDepth) {
      selectedLoopDepth = selected->loopDepth;
      lowered.selectedTemplate = selected->name;
    } else if (*selectedLoopDepth != selected->loopDepth) {
      lowered.unsupportedReason =
          "fusion group selects incompatible TileLib loop depths";
      return lowered;
    }

    const unsigned requiredOperands = elementwise->tileInputs +
                                      elementwise->scalarInputs +
                                      elementwise->tileOutputs;
    if (tileOp.op->getNumOperands() < requiredOperands) {
      lowered.unsupportedReason = "too few operands for tile op: " +
                                  tileOp.op->getName().getStringRef().str();
      return lowered;
    }
    const unsigned outputBase =
        tileOp.op->getNumOperands() - elementwise->tileOutputs;
    mlir::Value output = tileOp.op->getOperand(outputBase);
    if (!isTileLike(output)) {
      lowered.unsupportedReason = "expected tile output for tile op: " +
                                  tileOp.op->getName().getStringRef().str();
      return lowered;
    }
    auto shape = parseStaticTileShape(output.getType());
    if (!shape) {
      lowered.unsupportedReason =
          "cannot infer static loop shape for tile op: " +
          tileOp.op->getName().getStringRef().str();
      return lowered;
    }
    if (!loopShape) {
      loopShape = *shape;
    } else if (loopShape->rows != shape->rows ||
               loopShape->cols != shape->cols) {
      lowered.unsupportedReason =
          "fusion group selects TileOps with different iteration shapes";
      return lowered;
    }
  }

  lowered.traversal = *selectedLoopDepth == 1 ? TileTraversal::OneDimensional
                                              : TileTraversal::TwoDimensional;
  lowered.dtype = loopShape->dtype;
  lowered.vectorLanes = lanesForDType(loopShape->dtype);
  const int64_t colTripCount =
      (loopShape->cols + lowered.vectorLanes - 1) / lowered.vectorLanes;
  if (colTripCount == 1) {
    lowered.unrollTripCount = loopShape->rows;
    lowered.unrollDimension = UnrollLoopDimension::Row;
  } else {
    lowered.unrollTripCount = colTripCount;
    lowered.unrollDimension = UnrollLoopDimension::Col;
  }
  if (lowered.traversal == TileTraversal::OneDimensional) {
    lowered.modeledLoopTripCount =
        (loopShape->rows * loopShape->cols + lowered.vectorLanes - 1) /
        lowered.vectorLanes;
    lowered.unrollLoopId = "tile_flat_loop";
  } else {
    lowered.modeledLoopTripCount = colTripCount;
    lowered.unrollLoopId = "tile_col_loop";
  }

  CanonicalTileBuilder builder(*loopShape, lowered.traversal);
  llvm::DenseMap<mlir::Value, std::string> valueToRegister;
  llvm::DenseMap<mlir::Value, unsigned> internalUseCount;
  llvm::SmallVector<mlir::Value, 8> producedValues;
  std::vector<CanonicalNode> body;

  for (size_t opIndex = 0; opIndex < orderedOps.size(); ++opIndex) {
    const PlannedTileOpIR &tileOp = orderedOps[opIndex];
    const ElementwiseTemplate &elementwise = templates[opIndex];
    const unsigned outputBase =
        tileOp.op->getNumOperands() - elementwise.tileOutputs;
    for (unsigned input = 0; input < elementwise.tileInputs; ++input) {
      mlir::Value operand = tileOp.op->getOperand(input);
      if (valueToRegister.find(operand) != valueToRegister.end())
        ++internalUseCount[operand];
    }

    std::vector<std::string> inputs;
    for (unsigned input = 0; input < elementwise.tileInputs; ++input) {
      mlir::Value operand = tileOp.op->getOperand(input);
      if (!isTileLike(operand)) {
        lowered.unsupportedReason = "expected tile input for tile op: " +
                                    tileOp.op->getName().getStringRef().str();
        return lowered;
      }
      inputs.push_back(
          builder.materializeTileInput(operand, body, valueToRegister));
    }

    const std::string outputDtype =
        dtypeOf(tileOp.op->getOperand(outputBase)).value_or(loopShape->dtype);
    if (elementwise.scalarInputs > 0) {
      mlir::Value scalarOperand = tileOp.op->getOperand(elementwise.tileInputs);
      const std::string scalar =
          builder.materializeScalar(scalarOperand, outputDtype);
      if (!elementwise.scalarAsVector) {
        inputs.push_back(scalar);
      } else {
        inputs.push_back(builder.appendCompute("VBR", outputDtype, outputDtype,
                                               {scalar}, body));
      }
    }

    std::string opcode = elementwise.microOp.str();
    std::string form = outputDtype;
    if (stripPtoPrefix(tileOp.op->getName().getStringRef()) == "tcvt") {
      const std::string sourceDtype =
          dtypeOf(tileOp.op->getOperand(0)).value_or(loopShape->dtype);
      form = conversionForm(sourceDtype, outputDtype);
      if (sourceDtype == "fp16" && outputDtype == "fp32")
        opcode = "VCVT_F16_TO_F32";
      else if (sourceDtype == "fp32" && outputDtype == "fp16")
        opcode = "VCVT_F32_TO_F16";
      else {
        lowered.unsupportedReason =
            "unsupported tcvt dtype conversion: " + sourceDtype + " -> " +
            outputDtype;
        return lowered;
      }
    }

    const std::string outputRegister =
        builder.appendCompute(opcode, form, outputDtype, inputs, body);
    mlir::Value output = tileOp.op->getOperand(outputBase);
    valueToRegister[output] = outputRegister;
    producedValues.push_back(output);
  }

  for (mlir::Value value : producedValues) {
    auto use = internalUseCount.find(value);
    if (use != internalUseCount.end() && use->second > 0)
      continue;
    auto reg = valueToRegister.find(value);
    if (reg != valueToRegister.end())
      builder.appendStore(value, reg->second, body);
  }

  lowered.vfInfo = builder.takeVfInfo(std::move(body));
  lowered.vfInfo.source.emplace("frontend", std::string("ptoas-tileop"));
  const CanonicalValidationResult validation =
      validateCanonicalVfInfo(lowered.vfInfo);
  if (!validation.ok()) {
    const CanonicalValidationDiagnostic &diagnostic =
        validation.diagnostics.front();
    lowered.unsupportedReason =
        "canonical VfInfo validation failed: " + diagnostic.code + " at " +
        diagnostic.path;
  }
  return lowered;
}

CanonicalVfInfo
materializeTileUnrollCandidate(const LoweredTileGroupProgram &lowered,
                               unsigned factor, TileUnrollOrder order) {
  if (!lowered.supported())
    throw std::runtime_error("cannot materialize unsupported TileOp group");
  if (factor == 0 || lowered.unrollTripCount % factor != 0 ||
      lowered.modeledLoopTripCount % factor != 0)
    throw std::runtime_error("invalid TileOp unroll candidate factor");
  if (factor == 1)
    return lowered.vfInfo;

  CanonicalVfInfo candidate = lowered.vfInfo;
  bool found = false;
  candidate.context =
      rewriteUnrollTarget(candidate, candidate.context, lowered.unrollLoopId,
                          lowered.modeledLoopTripCount, factor, order, found);
  if (!found)
    throw std::runtime_error("canonical TileOp unroll loop was not found");
  const CanonicalValidationResult validation =
      validateCanonicalVfInfo(candidate);
  if (!validation.ok()) {
    const CanonicalValidationDiagnostic &diagnostic =
        validation.diagnostics.front();
    throw std::runtime_error("canonical unroll candidate validation failed: " +
                             diagnostic.code + " at " + diagnostic.path);
  }
  return candidate;
}

} // namespace vfsim
