// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/ParamCompat.h"

namespace vfsim::param_compat {
namespace {

std::string qualify(const std::string &op, const std::string &form) {
  return form.empty() ? op : op + "." + form;
}

} // namespace

std::string compatibleForm(const std::string &form) {
  if (form == "b16")
    return "fp16";
  if (form == "b32")
    return "fp32";
  return {};
}

std::vector<std::string> formCandidates(const std::string &form) {
  if (form.empty())
    return {};
  const std::string compatible = compatibleForm(form);
  if (!compatible.empty() && compatible != form)
    return {form, compatible};
  return {form};
}

std::vector<PairKeyCandidate>
pairKeyCandidates(const std::string &producerOp, const std::string &producerForm,
                  const std::string &consumerOp, const std::string &consumerForm) {
  std::vector<PairKeyCandidate> out;
  for (const std::string &pForm : formCandidates(producerForm)) {
    for (const std::string &cForm : formCandidates(consumerForm)) {
      out.push_back(PairKeyCandidate{
          qualify(producerOp, pForm),
          qualify(consumerOp, cForm),
          pForm != producerForm || cForm != consumerForm,
      });
    }
  }
  return out;
}

} // namespace vfsim::param_compat
