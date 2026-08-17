// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_API_NATIVE_INSTRUCTION_CATALOG_H
#define VFSIM_API_NATIVE_INSTRUCTION_CATALOG_H

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace vfsim {

enum class CatalogInstructionClass { Load, Store, Compute, Control };
enum class CatalogOperandDirection { Input, Output, Ignore };
enum class CatalogArgumentKind {
  Register,
  UB,
  Scalar,
  Predicate,
  Config,
  RegisterOrScalar
};

struct NativeOperandSpec {
  std::string name;
  int argumentIndex = 0;
  CatalogOperandDirection direction = CatalogOperandDirection::Input;
  std::string role;
  CatalogArgumentKind kind = CatalogArgumentKind::Register;
  bool optional = false;
  std::unordered_set<std::string> allowedValues;
};

struct NativeInstructionSpec {
  std::string opcode;
  CatalogInstructionClass instructionClass = CatalogInstructionClass::Compute;
  std::string signature;
  std::string formRule;
  std::string fixedForm;
  bool virtualOpcode = false;
  std::unordered_set<std::string> forms;
  std::unordered_map<std::string, std::string> specializations;
  std::vector<NativeOperandSpec> operands;
};

class InstructionCatalog {
public:
  std::string canonicalOpcode(const std::string &opcode) const;
  std::string specializeOpcode(const std::string &opcode,
                               const std::string &form) const;
  const NativeInstructionSpec *lookup(const std::string &opcode) const;

private:
  friend const InstructionCatalog &defaultInstructionCatalog();
  InstructionCatalog();

  std::unordered_map<std::string, NativeInstructionSpec> specs_;
  std::unordered_map<std::string, std::string> aliases_;
};

const InstructionCatalog &defaultInstructionCatalog();

} // namespace vfsim

#endif // VFSIM_API_NATIVE_INSTRUCTION_CATALOG_H
