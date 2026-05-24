#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void regress_gelu_poly_i16_u1_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_times) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_0;
        vector_f32 vec_1;
        vector_f32 vec_2;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f32 vec_5;
        vector_f32 vec_6;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
            vlds(vec_0, memA, 64 * i, NORM);
            vmuls(vec_1, vec_0, 0.5, pat_all_b32);
            vmuls(vec_2, vec_0, 0.7071, pat_all_b32);
            vmins(vec_2, vec_2, 3.92, pat_all_b32);
            vmaxs(vec_2, vec_2, -3.92, pat_all_b32);
            vmul(vec_3, vec_2, vec_2, pat_all_b32);
            vmuls(vec_4, vec_3, 0.5344, pat_all_b32);
            vadds(vec_4, vec_4, 7.5517, pat_all_b32);
            vmul(vec_4, vec_4, vec_3, pat_all_b32);
            vadds(vec_4, vec_4, 101.62809, pat_all_b32);
            vmul(vec_4, vec_4, vec_3, pat_all_b32);
            vadds(vec_4, vec_4, 1393.8015, pat_all_b32);
            vmul(vec_4, vec_4, vec_3, pat_all_b32);
            vadds(vec_4, vec_4, 5063.7915, pat_all_b32);
            vmul(vec_4, vec_4, vec_3, pat_all_b32);
            vadds(vec_4, vec_4, 29639.3848, pat_all_b32);
            vmul(vec_4, vec_4, vec_2, pat_all_b32);
            vadds(vec_5, vec_3, 31.2128582, pat_all_b32);
            vmul(vec_5, vec_5, vec_3, pat_all_b32);
            vadds(vec_5, vec_5, 308.569641, pat_all_b32);
            vmul(vec_5, vec_5, vec_3, pat_all_b32);
            vadds(vec_5, vec_5, 3023.12476, pat_all_b32);
            vmul(vec_5, vec_5, vec_3, pat_all_b32);
            vadds(vec_5, vec_5, 14243.3662, pat_all_b32);
            vmul(vec_5, vec_5, vec_3, pat_all_b32);
            vadds(vec_5, vec_5, 26267.2246, pat_all_b32);
            vdiv(vec_5, vec_4, vec_5, pat_all_b32);
            vadds(vec_5, vec_5, 1.0, pat_all_b32);
            vmul(vec_6, vec_5, vec_1, pat_all_b32);
            vsts(vec_6, memB, 64 * i, NORM_B32, pat_all_b32);
        }

    }
}

extern "C" __global__ __aicore__ void foo_add(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0)
{
    __ubuf__ float *ub_data_x_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_y_addr_0 = (__ubuf__ float*)get_imm(0x1000);
    int repeat_times = 16;
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)for_loop0_input0,
                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    regress_gelu_poly_i16_u1_simd_ub(
        ub_data_x_addr_0,
        ub_data_y_addr_0,
        repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_y_addr_0,
        0, 1, repeat_times*64*4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}