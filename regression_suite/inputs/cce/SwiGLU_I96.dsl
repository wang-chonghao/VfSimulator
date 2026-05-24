#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void swiglu_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    __ubuf__ float *memC,
    int repeat_times) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_gate;      // a
        vector_f32 vec_up;        // b
        vector_f32 vec_neg;
        vector_f32 vec_den;
        vector_f32 vec_one;
        vector_f32 vec_sigmoid;
        vector_f32 vec_silu;
        vector_f32 vec_out;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
            // a, b
            vlds(vec_gate, memA, 64 * i, NORM);
            vlds(vec_up, memB, 64 * i, NORM);

            // sigmoid(a) = 1 / (1 + exp(-a))
            vmuls(vec_neg, vec_gate, -1.0, pat_all_b32);
            vexp(vec_den, vec_neg, pat_all_b32);
            vadds(vec_den, vec_den, 1.0, pat_all_b32);
            vmuls(vec_one, vec_gate, 0.0, pat_all_b32);
            vadds(vec_one, vec_one, 1.0, pat_all_b32);
            vdiv(vec_sigmoid, vec_one, vec_den, pat_all_b32);

            // SiLU(a)
            vmul(vec_silu, vec_gate, vec_sigmoid, pat_all_b32);
            // SwiGLU(a, b) = SiLU(a) * b
            vmul(vec_out, vec_silu, vec_up, pat_all_b32);
            vsts(vec_out, memC, 64 * i, NORM_B32, pat_all_b32);
        }
    }
}

extern "C" __global__ __aicore__ void swiglu_kernel(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0)
{
    __ubuf__ float *ub_data_a_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_b_addr_0 = (__ubuf__ float*)get_imm(0x10000);
    __ubuf__ float *ub_data_o_addr_0 = (__ubuf__ float*)get_imm(0x20000);
    int repeat_times = 96;

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_a_addr_0, (__gm__ float*)for_loop0_input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_b_addr_0, (__gm__ float*)for_loop0_input1,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    swiglu_simd_ub(ub_data_a_addr_0, ub_data_b_addr_0, ub_data_o_addr_0, repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_o_addr_0,
        0, 1, 16 * 1024, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}
