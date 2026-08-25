// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/CanonicalProgramLowering.h"

#include "api/native/InstructionCatalog.h"
#include "api/native/UarchOverrideSchema.h"

#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace vfsim {
namespace {

ValueStorageKind storageKind(CanonicalStorageKind storage) {
  switch (storage) {
  case CanonicalStorageKind::Register:
    return ValueStorageKind::Register;
  case CanonicalStorageKind::UB:
    return ValueStorageKind::UB;
  case CanonicalStorageKind::Scalar:
    return ValueStorageKind::Scalar;
  case CanonicalStorageKind::Unknown:
    break;
  }
  throw std::runtime_error("Canonical value has unknown storage");
}

int64_t integerOverride(const CanonicalScalar &value,
                        const std::string &name) {
  if (const auto *integer = std::get_if<int64_t>(&value))
    return *integer;
  throw std::runtime_error("Canonical uarch override " + name +
                           " must be an integer");
}

bool booleanOverride(const CanonicalScalar &value, const std::string &name) {
  if (const auto *boolean = std::get_if<bool>(&value))
    return *boolean;
  throw std::runtime_error("Canonical uarch override " + name +
                           " must be a boolean");
}

std::string stringOverride(const CanonicalScalar &value,
                           const std::string &name) {
  if (const auto *text = std::get_if<std::string>(&value))
    return *text;
  throw std::runtime_error("Canonical uarch override " + name +
                           " must be a string");
}

int64_t resolveInteger(const CanonicalIntegerExpression &expression,
                       const RuntimeParamMap &params,
                       const std::string &field) {
  if (const auto *value = std::get_if<int64_t>(&expression))
    return *value;
  const std::string &text = std::get<std::string>(expression);
  const auto parameter = params.find(text);
  if (parameter != params.end())
    return parameter->second;
  try {
    size_t consumed = 0;
    const int64_t value = std::stoll(text, &consumed, 10);
    if (consumed == text.size())
      return value;
  } catch (...) {
  }
  throw std::runtime_error("Cannot resolve canonical " + field + ": " + text);
}

struct Frame {
  int64_t numericId = 0;
  std::string loopId;
  int64_t iteration = 0;
  int64_t scheduledIteration = 0;
  int64_t topBlockId = 0;
};

class Expander {
public:
  explicit Expander(const CanonicalVfInfo &vfInfo, const ParamDB *db)
      : vfInfo_(vfInfo), db_(db) {
    runtime_.params = vfInfo.params;
    for (const auto &[definitionId, value] : vfInfo.values) {
      ValueInfo lowered;
      lowered.valueId = definitionId;
      lowered.storage = storageKind(value.storage);
      lowered.dtype = value.dtype;
      lowered.shape = value.shape;
      runtime_.values.emplace(definitionId, std::move(lowered));
    }
    indexLoops(vfInfo.context, 0);
  }

  CanonicalRuntimeProgram run() {
    const auto validation = validateCanonicalVfInfo(vfInfo_);
    if (!validation.ok()) {
      std::ostringstream message;
      message << "CanonicalVfInfo validation failed";
      for (const auto &diagnostic : validation.diagnostics)
        message << "; " << diagnostic.code << " at " << diagnostic.path;
      throw std::runtime_error(message.str());
    }
    expandTopLevelNodes(vfInfo_.context);
    annotateLifetimes();
    annotateSchedulingBoundaries();
    runtime_.totalTopBlocks = std::max<int64_t>(1, nextTopBlockId_);
    std::unordered_set<int64_t> nonemptyTopBlocks;
    for (const auto &instruction : runtime_.instructions) {
      if (instruction.type == "inst")
        nonemptyTopBlocks.insert(instruction.topBlockId);
    }
    for (int64_t topBlockId = 0; topBlockId < runtime_.totalTopBlocks;
         ++topBlockId) {
      if (!nonemptyTopBlocks.count(topBlockId))
        runtime_.emptyTopBlocks.insert(topBlockId);
    }
    if (runtime_.topBlockLoopBounds.empty())
      runtime_.topBlockLoopBounds.emplace(0, std::vector<int64_t>{});
    return std::move(runtime_);
  }

private:
  const CanonicalVfInfo &vfInfo_;
  const ParamDB *db_ = nullptr;
  CanonicalRuntimeProgram runtime_;
  std::unordered_map<std::string, std::string> bindings_;
  std::unordered_map<std::string, int64_t> loopNumericIds_;
  std::unordered_map<std::string, int64_t> loopTopBlockIds_;
  std::vector<Frame> frames_;
  int64_t nextLoopId_ = 0;
  int64_t nextTopBlockId_ = 0;
  int64_t activeTopBlockId_ = 0;

  void indexLoops(const std::vector<CanonicalNode> &nodes, int depth) {
    for (const auto &node : nodes) {
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr)
        continue;
      const CanonicalLoop &loop = **loopPtr;
      loopNumericIds_[loop.loopId] = nextLoopId_++;
      if (depth == 0)
        loopTopBlockIds_[loop.loopId] = nextTopBlockId_++;
      indexLoops(loop.body, depth + 1);
    }
  }

  std::string resolve(const std::string &definitionId) const {
    const auto found = bindings_.find(definitionId);
    return found == bindings_.end() ? definitionId : found->second;
  }

  std::string dynamicName(const std::string &definitionId) {
    if (frames_.empty())
      return definitionId;
    std::ostringstream name;
    name << definitionId;
    for (const auto &frame : frames_)
      name << "@" << frame.loopId << "=" << frame.iteration;
    const std::string identity = name.str();
    const auto source = vfInfo_.values.find(definitionId);
    if (source != vfInfo_.values.end()) {
      ValueInfo value;
      value.valueId = identity;
      value.storage = storageKind(source->second.storage);
      value.dtype = source->second.dtype;
      value.shape = source->second.shape;
      runtime_.values.emplace(identity, std::move(value));
    }
    return identity;
  }

  void fillDynamicMetadata(DynamicInst &inst) const {
    inst.topBlockId =
        frames_.empty() ? activeTopBlockId_ : frames_.front().topBlockId;
    for (const auto &frame : frames_) {
      inst.loopStack.push_back(frame.numericId);
      inst.iterStack.push_back(frame.scheduledIteration);
      inst.iterationPath.emplace_back(frame.loopId, frame.iteration);
    }
    inst.loopDepth = static_cast<int64_t>(frames_.size());
    inst.inLoop = !frames_.empty();
  }

  void expandInstruction(const CanonicalInstruction &instruction) {
    if (!instruction.dependencies.empty())
      throw std::runtime_error(
          "Canonical explicit dependencies are not supported by native Core: " +
          instruction.instructionId);
    const auto *catalog = defaultInstructionCatalog().lookup(instruction.opcode);
    if (catalog == nullptr &&
        instruction.instructionClass != CanonicalInstructionClass::Compute)
      throw std::runtime_error(
          "Unknown native canonical load/store/control opcode: " +
          instruction.opcode);
    if (instruction.instructionClass == CanonicalInstructionClass::Control)
      throw std::runtime_error(
          "Canonical control instruction must use a Membar node: " +
          instruction.instructionId);

    DynamicInst dynamic;
    dynamic.type = "inst";
    dynamic.op = instruction.opcode;
    dynamic.form = instruction.form;
    dynamic.staticInstructionId = instruction.instructionId;
    const auto alignOperation = instruction.attributes.find("align_state_operation");
    if (alignOperation != instruction.attributes.end()) {
      const auto *value = std::get_if<std::string>(&alignOperation->second);
      if (value == nullptr)
        throw std::runtime_error(
            "align_state_operation must be a string: " +
            instruction.instructionId);
      dynamic.alignStateOperation = *value;
    }
    const auto alignState = instruction.attributes.find("align_state_id");
    if (alignState != instruction.attributes.end()) {
      const auto *value = std::get_if<std::string>(&alignState->second);
      if (value == nullptr)
        throw std::runtime_error("align_state_id must be a string: " +
                                 instruction.instructionId);
      dynamic.alignStateId = *value;
    }
    fillDynamicMetadata(dynamic);
    for (const auto &operand : instruction.inputs)
      dynamic.src.push_back(resolve(operand.valueId));
    for (const auto &operand : instruction.outputs) {
      const std::string identity = dynamicName(operand.valueId);
      dynamic.dst.push_back(identity);
      bindings_[operand.valueId] = identity;
    }
    runtime_.instructions.push_back(std::move(dynamic));
  }

  void expandMembar(const CanonicalMembar &membar) {
    if (!membar.dependencies.empty())
      throw std::runtime_error(
          "Canonical explicit Membar dependencies are not supported: " +
          membar.instructionId);
    DynamicInst dynamic;
    dynamic.type = "membar";
    dynamic.barrier = membar.barrier;
    dynamic.staticInstructionId = membar.instructionId;
    fillDynamicMetadata(dynamic);
    runtime_.instructions.push_back(std::move(dynamic));
  }

  bool containsLoop(const CanonicalLoop &loop) const {
    return std::any_of(loop.body.begin(), loop.body.end(), [](const auto &node) {
      return std::holds_alternative<std::shared_ptr<const CanonicalLoop>>(
          node.payload);
    });
  }

  bool containsMembar(const CanonicalLoop &loop) const {
    return std::any_of(loop.body.begin(), loop.body.end(), [](const auto &node) {
      return std::holds_alternative<CanonicalMembar>(node.payload);
    });
  }

  std::vector<int64_t> nestedBounds(const CanonicalLoop &loop) const {
    std::vector<int64_t> bounds{
        resolveInteger(loop.count, vfInfo_.params, "loop count")};
    for (const auto &node : loop.body) {
      const auto *nested =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (nested != nullptr && *nested) {
        auto child = nestedBounds(**nested);
        bounds.insert(bounds.end(), child.begin(), child.end());
        break;
      }
    }
    return bounds;
  }

  void expandLoop(const CanonicalLoop &loop, int depth) {
    const int64_t count = resolveInteger(loop.count, vfInfo_.params, "loop count");
    int64_t unroll = resolveInteger(loop.unroll, vfInfo_.params, "loop unroll");
    if (count < 0 || unroll <= 0)
      throw std::runtime_error("Invalid canonical loop count/unroll: " + loop.loopId);
    const bool innermost = !containsLoop(loop);
    if (!innermost && unroll > 1)
      throw std::runtime_error(
          "Canonical non-innermost loop cannot use unroll > 1: " +
          loop.loopId);
    const bool hasMembar = containsMembar(loop);
    if (hasMembar && unroll > 1) {
      if (db_ != nullptr) {
        const auto membar = std::find_if(
            loop.body.begin(), loop.body.end(), [](const auto &node) {
              return std::holds_alternative<CanonicalMembar>(node.payload);
            });
        db_->recordWarning(
            "membar_unroll_disabled",
            {
                {"pc", "-1"},
                {"barrier", std::get<CanonicalMembar>(membar->payload).barrier},
                {"loop_id", loop.loopId},
                {"requested_unroll", std::to_string(unroll)},
                {"used_unroll", "1"},
                {"reason", "membar_in_unrolled_innermost_loop"},
            });
      }
      unroll = 1;
    }
    if (count > 0 && count % unroll != 0)
      throw std::runtime_error("Canonical loop count is not divisible by unroll: " +
                               loop.loopId);

    const int64_t topBlock = depth == 0
                                 ? loopTopBlockIds_.at(loop.loopId)
                                 : frames_.front().topBlockId;
    if (depth == 0)
      runtime_.topBlockLoopBounds[static_cast<int>(topBlock)] =
          nestedBounds(loop);

    const auto outerBindings = bindings_;
    std::vector<std::string> currentValues;
    currentValues.reserve(loop.carriedValues.size());
    for (const auto &carried : loop.carriedValues)
      currentValues.push_back(resolve(carried.entryValueId));

    for (int64_t base = 0; base < count; base += unroll) {
      std::vector<std::vector<DynamicInst>> lanes;
      for (int64_t lane = 0; lane < unroll; ++lane) {
        const int64_t iteration = base + lane;
        for (size_t index = 0; index < loop.carriedValues.size(); ++index)
          bindings_[loop.carriedValues[index].entryValueId] = currentValues[index];
        frames_.push_back(Frame{loopNumericIds_.at(loop.loopId), loop.loopId,
                                iteration, base / unroll, topBlock});
        const size_t start = runtime_.instructions.size();
        expandNodes(loop.body, depth + 1);
        std::vector<DynamicInst> laneInstructions(
            std::make_move_iterator(runtime_.instructions.begin() +
                                    static_cast<std::ptrdiff_t>(start)),
            std::make_move_iterator(runtime_.instructions.end()));
        runtime_.instructions.erase(
            runtime_.instructions.begin() + static_cast<std::ptrdiff_t>(start),
            runtime_.instructions.end());
        frames_.pop_back();
        for (size_t index = 0; index < loop.carriedValues.size(); ++index)
          currentValues[index] =
              resolve(loop.carriedValues[index].backEdgeValueId);
        lanes.push_back(std::move(laneInstructions));
      }
      if (unroll == 1) {
        for (auto &instruction : lanes.front())
          runtime_.instructions.push_back(std::move(instruction));
      } else {
        const size_t width = lanes.front().size();
        if (std::any_of(lanes.begin(), lanes.end(), [width](const auto &lane) {
              return lane.size() != width;
            }))
          throw std::runtime_error(
              "Canonical unroll lanes produced inconsistent instruction counts");
        for (size_t instructionIndex = 0; instructionIndex < width;
             ++instructionIndex)
          for (auto &lane : lanes)
            runtime_.instructions.push_back(
                std::move(lane[instructionIndex]));
      }
    }

    bindings_ = outerBindings;
    for (size_t index = 0; index < loop.carriedValues.size(); ++index) {
      const auto &carried = loop.carriedValues[index];
      bindings_[carried.exitValueId] =
          count == 0 ? resolve(carried.entryValueId) : currentValues[index];
    }
  }

  void expandNodes(const std::vector<CanonicalNode> &nodes, int depth) {
    for (const auto &node : nodes) {
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        expandInstruction(*instruction);
      } else if (const auto *membar =
                     std::get_if<CanonicalMembar>(&node.payload)) {
        expandMembar(*membar);
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        expandLoop(*loop, depth);
      }
    }
  }

  void expandTopLevelNodes(const std::vector<CanonicalNode> &nodes) {
    for (const auto &node : nodes) {
      const auto *loop =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loop != nullptr) {
        if (!*loop)
          throw std::runtime_error("Canonical loop payload is null");
        activeTopBlockId_ = loopTopBlockIds_.at((*loop)->loopId);
        expandLoop(**loop, 0);
        continue;
      }

      // Top-level straight-line nodes after a loop belong to that loop's
      // scheduling block until the next top-level loop starts. This keeps a
      // loop epilogue (for example VSTAS + Membar) ahead of the next VLOOP.
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload))
        expandInstruction(*instruction);
      else
        expandMembar(std::get<CanonicalMembar>(node.payload));
    }
  }

  void annotateLifetimes() {
    std::unordered_map<std::string, int64_t> remainingUses;
    for (const auto &instruction : runtime_.instructions)
      for (const auto &source : instruction.src)
        ++remainingUses[source];

    int64_t instructionId = 0;
    int64_t streamSequence = 0;
    for (auto &instruction : runtime_.instructions) {
      instruction.streamSeq = streamSequence++;
      instruction.instId = instruction.type == "membar" ? -1 : instructionId++;
      for (const auto &destination : instruction.dst)
        instruction.dstValueKeep.push_back(remainingUses[destination] > 0);
      for (const auto &source : instruction.src) {
        const int64_t remaining = --remainingUses[source];
        instruction.srcValueRelease.push_back(remaining == 0);
      }
    }
  }

  void annotateSchedulingBoundaries() {
    std::unordered_map<std::string, size_t> lastByBlock;
    std::unordered_map<int64_t, size_t> lastByTopBlock;
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      lastByTopBlock[instruction.topBlockId] = index;
      for (size_t level = 0; level < instruction.loopStack.size(); ++level) {
        std::vector<int64_t> prefix(instruction.iterStack.begin(),
                                    instruction.iterStack.begin() +
                                        static_cast<std::ptrdiff_t>(level));
        instruction.blockKeyByLevel.emplace_back(
            "loop" + std::to_string(level), prefix);
        std::ostringstream key;
        key << instruction.topBlockId << ":" << level;
        for (const auto value : prefix)
          key << ":" << value;
        lastByBlock[key.str()] = index;
      }
    }
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      for (int64_t level = static_cast<int64_t>(instruction.loopStack.size()) - 1;
           level >= 0; --level) {
        std::ostringstream key;
        key << instruction.topBlockId << ":" << level;
        for (int64_t prefix = 0; prefix < level; ++prefix)
          key << ":" << instruction.iterStack[static_cast<size_t>(prefix)];
        if (lastByBlock[key.str()] == index)
          instruction.blockEndLevels.push_back(level);
      }
      instruction.isLastInTopBlock =
          lastByTopBlock[instruction.topBlockId] == index;
    }
  }
};

const std::unordered_map<std::string, int64_t UarchConfig::*> &
integerUarchOverrideFields() {
  static const std::unordered_map<std::string, int64_t UarchConfig::*> fields{
      {"issue_ports", &UarchConfig::issuePorts},
      {"load_ports", &UarchConfig::loadPorts},
      {"store_ports", &UarchConfig::storePorts},
      {"ub_slots", &UarchConfig::ubSlots},
      {"lsu_store_priority_preg_threshold",
       &UarchConfig::lsuStorePriorityPregThreshold},
      {"IDU_window_width", &UarchConfig::iduWindowWidth},
      {"IDU_issue_width", &UarchConfig::iduIssueWidth},
      {"LDQ_width", &UarchConfig::ldqWidth},
      {"vreg_num", &UarchConfig::vregNum},
      {"shq_depth", &UarchConfig::shqDepth},
      {"exq_depth", &UarchConfig::exqDepth},
      {"shq_release_delay", &UarchConfig::shqReleaseDelay},
      {"idu_visible_preg_delay", &UarchConfig::iduVisiblePregDelay},
      {"idu_visible_shq_delay", &UarchConfig::iduVisibleShqDelay},
      {"idu_to_ooo_delay", &UarchConfig::iduToOooDelay},
      {"vloop_to_dispatch_delay", &UarchConfig::vloopToDispatchDelay},
      {"idu_dispatch_start_advance", &UarchConfig::iduDispatchStartAdvance},
      {"initial_top_block_vloop_start_cycle",
       &UarchConfig::initialTopBlockVloopStartCycle},
      {"nested_vloop_initial_start_gap",
       &UarchConfig::nestedVloopInitialStartGap},
      {"loop1_min_feedback_gap", &UarchConfig::loop1MinFeedbackGap},
      {"innermost_iter_dispatch_stride",
       &UarchConfig::innermostIterDispatchStride},
      {"consumer_release_start_offset",
       &UarchConfig::consumerReleaseStartOffset},
      {"ooo_to_shq_delay", &UarchConfig::oooToShqDelay},
      {"ooo_to_lsq_delay", &UarchConfig::oooToLsqDelay},
      {"exq_recv_delay", &UarchConfig::exqRecvDelay},
      {"shq_to_exq_port_per_cycle", &UarchConfig::shqToExqPortPerCycle},
      {"exu0_reserve_lookahead", &UarchConfig::exu0ReserveLookahead},
      {"exu0_reserve_min_count", &UarchConfig::exu0ReserveMinCount},
      {"compute_inflight_cap", &UarchConfig::computeInflightCap},
      {"exq_issue_inflight_cap_per_port",
       &UarchConfig::exqIssueInflightCapPerPort},
  };
  return fields;
}

const std::unordered_map<std::string, bool UarchConfig::*> &
booleanUarchOverrideFields() {
  static const std::unordered_map<std::string, bool UarchConfig::*> fields{
      {"three_ports_mode", &UarchConfig::threePortsMode},
      {"enable_isu_queue_model", &UarchConfig::enableIsuQueueModel},
      {"admit_blocked_to_exq", &UarchConfig::admitBlockedToExq},
      {"enable_shq_credit_model", &UarchConfig::enableShqCreditModel},
      {"enable_credit_visibility_delay",
       &UarchConfig::enableCreditVisibilityDelay},
      {"global_shq_preg_gate", &UarchConfig::globalShqPregGate},
      {"use_explicit_idu_credit_bank",
       &UarchConfig::useExplicitIduCreditBank},
      {"exq_capacity_counts_inflight",
       &UarchConfig::exqCapacityCountsInflight},
      {"enforce_same_cycle_src_hazard",
       &UarchConfig::enforceSameCycleSrcHazard},
      {"enable_cross_fu_ii", &UarchConfig::enableCrossFuIi},
  };
  return fields;
}

const std::set<std::string> &stringUarchOverrideFields() {
  static const std::set<std::string> fields{"shq_exq_dispatch_policy"};
  return fields;
}

} // namespace

CanonicalRuntimeProgram lowerCanonicalProgram(const CanonicalVfInfo &vfInfo,
                                               const ParamDB *db) {
  return Expander(vfInfo, db).run();
}

UarchConfig resolveCanonicalUarch(const CanonicalVfInfo &vfInfo,
                                  const UarchConfig &defaults) {
  UarchConfig resolved = defaults;
  const auto &integerFields = integerUarchOverrideFields();
  const auto &booleanFields = booleanUarchOverrideFields();

  for (const auto &[name, value] : vfInfo.uarch) {
    if (isDeprecatedUarchOverrideField(name))
      throw std::runtime_error(
          "Canonical uarch override " + name + " is deprecated");
    if (!uarchOverrideFieldSupportsTarget(name, UarchOverrideTarget::Cpp))
      throw std::runtime_error("Canonical uarch override " + name +
                               " is not supported by the C++ target");
    if (const auto found = integerFields.find(name); found != integerFields.end()) {
      resolved.*(found->second) = integerOverride(value, name);
      continue;
    }
    if (const auto found = booleanFields.find(name); found != booleanFields.end()) {
      resolved.*(found->second) = booleanOverride(value, name);
      continue;
    }
    if (stringUarchOverrideFields().count(name))
      resolved.shqExqDispatchPolicy = stringOverride(value, name);
    else
      throw std::runtime_error("C++ uarch schema/resolver mismatch for " + name);
  }
  return resolved;
}

std::set<std::string> cppResolvedUarchOverrideFields() {
  std::set<std::string> result = stringUarchOverrideFields();
  for (const auto &[name, unused] : integerUarchOverrideFields())
    result.emplace(name);
  for (const auto &[name, unused] : booleanUarchOverrideFields())
    result.emplace(name);
  return result;
}

} // namespace vfsim
