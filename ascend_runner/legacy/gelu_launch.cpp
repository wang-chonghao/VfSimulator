#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

#include "acl/acl.h"

extern "C" __global__ __aicore__ void foo_add(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0);

void LaunchKernel(void *stream, float *input0, float *input1, float *output0)
{
    foo_add<<<1, nullptr, stream>>>(input0, input1, output0);
}
