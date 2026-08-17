// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_CANONICAL_VF_INFO_H
#define VFSIM_API_NATIVE_CANONICAL_VF_INFO_H

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace vfsim {

constexpr int64_t kCanonicalVfInfoSchemaVersion = 1;

using CanonicalScalar = std::variant<std::monostate, bool, int64_t, double, std::string>;
using CanonicalIntegerExpression = std::variant<int64_t, std::string>;

enum class CanonicalStorageKind { Register, UB, Scalar };
enum class CanonicalInstructionClass { Load, Store, Compute, Control };
enum class CanonicalOperandRole { Source, Destination, Memory, Scalar, Predicate, Config };
enum class CanonicalAccessKind { Read, Write };
enum class CanonicalDependencyKind { Data, Memory, Control };

struct CanonicalSourceLocation {
  std::optional<std::string> source;
  std::optional<int64_t> line;
  std::optional<int64_t> column;
  std::optional<std::string> path;
};

struct CanonicalAffineTerm {
  std::string variableId;
  int64_t coefficient = 1;
};

struct CanonicalAffineExpression {
  int64_t constant = 0;
  std::vector<CanonicalAffineTerm> terms;
};

struct CanonicalMemoryAccess {
  std::string baseValueId;
  CanonicalAffineExpression offset;
  CanonicalAccessKind accessKind = CanonicalAccessKind::Read;
  std::optional<int64_t> span;
  std::optional<std::string> aliasGroup;
};

struct CanonicalValue {
  std::string definitionId;
  std::string logicalId;
  CanonicalStorageKind storage = CanonicalStorageKind::Register;
  std::string dtype;
  std::vector<int64_t> shape;
  std::optional<std::string> producerInstructionId;
  std::optional<CanonicalSourceLocation> sourceLocation;
};

struct CanonicalOperand {
  std::string valueId;
  CanonicalOperandRole role = CanonicalOperandRole::Source;
  std::optional<std::string> dtype;
  std::optional<CanonicalMemoryAccess> memoryAccess;
};

struct CanonicalDependencyRef {
  std::string producerInstructionId;
  CanonicalDependencyKind kind = CanonicalDependencyKind::Data;
  std::optional<int64_t> operandIndex;
};

struct CanonicalInstruction {
  std::string instructionId;
  std::string opcode;
  CanonicalInstructionClass instructionClass = CanonicalInstructionClass::Compute;
  std::string form;
  std::vector<CanonicalOperand> inputs;
  std::vector<CanonicalOperand> outputs;
  std::vector<CanonicalDependencyRef> dependencies;
  std::map<std::string, CanonicalScalar> attributes;
  std::optional<CanonicalSourceLocation> sourceLocation;
};

struct CanonicalMembar {
  std::string instructionId;
  std::string barrier;
  std::vector<CanonicalDependencyRef> dependencies;
  std::optional<CanonicalSourceLocation> sourceLocation;
};

struct CanonicalInductionVariable {
  std::string variableId;
  CanonicalIntegerExpression start = int64_t{0};
  CanonicalIntegerExpression step = int64_t{1};
};

struct CanonicalLoopCarriedValue {
  std::string logicalId;
  std::string entryValueId;
  std::string backEdgeValueId;
  std::string exitValueId;
};

struct CanonicalLoop;

struct CanonicalNode {
  using Payload = std::variant<CanonicalInstruction,
                               std::shared_ptr<CanonicalLoop>,
                               CanonicalMembar>;
  Payload payload;

  static CanonicalNode makeInstruction(CanonicalInstruction value);
  static CanonicalNode makeLoop(CanonicalLoop value);
  static CanonicalNode makeMembar(CanonicalMembar value);
};

struct CanonicalLoop {
  std::string loopId;
  CanonicalInductionVariable induction;
  CanonicalIntegerExpression count = int64_t{1};
  CanonicalIntegerExpression unroll = int64_t{1};
  std::vector<CanonicalLoopCarriedValue> carriedValues;
  std::vector<CanonicalNode> body;
  std::optional<CanonicalSourceLocation> sourceLocation;
};

struct CanonicalVfInfo {
  int64_t schemaVersion = kCanonicalVfInfoSchemaVersion;
  std::vector<CanonicalNode> context;
  std::unordered_map<std::string, CanonicalValue> values;
  std::unordered_map<std::string, int64_t> params;
  std::map<std::string, CanonicalScalar> uarch;
  std::map<std::string, CanonicalScalar> source;
};

struct CanonicalValidationDiagnostic {
  std::string code;
  std::string severity = "error";
  std::string message;
  std::string path;
  std::optional<CanonicalSourceLocation> sourceLocation;
  std::map<std::string, CanonicalScalar> context;
};

struct CanonicalValidationResult {
  std::vector<CanonicalValidationDiagnostic> diagnostics;
  bool ok() const;
};

CanonicalValidationResult validateCanonicalVfInfo(const CanonicalVfInfo &vfInfo);

} // namespace vfsim

#endif // VFSIM_API_NATIVE_CANONICAL_VF_INFO_H
