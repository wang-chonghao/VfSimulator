// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_PARAM_COMPAT_H
#define VFSIM_NATIVE_PARAM_COMPAT_H

#include <string>
#include <vector>

namespace vfsim::param_compat {

std::string compatibleForm(const std::string &form);
std::vector<std::string> formCandidates(const std::string &form);

struct PairKeyCandidate {
  std::string producer;
  std::string consumer;
  bool usedCompatible = false;
};

std::vector<PairKeyCandidate>
pairKeyCandidates(const std::string &producerOp, const std::string &producerForm,
                  const std::string &consumerOp, const std::string &consumerForm);

} // namespace vfsim::param_compat

#endif // VFSIM_NATIVE_PARAM_COMPAT_H
