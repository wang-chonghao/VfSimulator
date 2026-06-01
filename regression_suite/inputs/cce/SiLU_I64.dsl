#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void silu_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_times) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_x;
        vector_f32 vec_negx;
        vector_f32 vec_den;
        vector_f32 vec_one;
        vector_f32 vec_sigmoid;
        vector_f32 vec_out;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
            // x
            vlds(vec_x, memA, 64 * i, NORM);
            // exp(-x)
            vmuls(vec_negx, vec_x, -1.0, pat_all_b32);
            vexp(vec_den, vec_negx, pat_all_b32);
            // 1 + exp(-x)
            vadds(vec_den, vec_den, 1.0, pat_all_b32);
            // one vector
            vmuls(vec_one, vec_x, 0.0, pat_all_b32);
            vadds(vec_one, vec_one, 1.0, pat_all_b32);
            // sigmoid(x) = 1 / (1 + exp(-x))
            vdiv(vec_sigmoid, vec_one, vec_den, pat_all_b32);
            // SiLU(x) = x * sigmoid(x)
            vmul(vec_out, vec_x, vec_sigmoid, pat_all_b32);
            vsts(vec_out, memB, 64 * i, NORM_B32, pat_all_b32);
        }
    }
}

extern "C" __global__ __aicore__ void silu_kernel(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0)
{
    __ubuf__ float *ub_data_x_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_y_addr_0 = (__ubuf__ float*)get_imm(0x10000);
    int repeat_times = 64;

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)for_loop0_input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    silu_simd_ub(ub_data_x_addr_0, ub_data_y_addr_0, repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_y_addr_0,
        0, 1, 16 * 1024, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}
