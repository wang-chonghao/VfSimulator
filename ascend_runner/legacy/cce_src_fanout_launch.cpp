#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

#include "acl/acl.h"

extern "C" __global__ __aicore__ void src_fanout_probe(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ output0);

void LaunchKernel(void *stream, float *input0, float *output0)
{
    src_fanout_probe<<<1, nullptr, stream>>>(input0, output0);
}
