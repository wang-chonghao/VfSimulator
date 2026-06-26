#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void tadd_tcvt_tadd_simd_ub(
    __ubuf__ float *mem0,
    __ubuf__ float *mem2,
    __ubuf__ half *mem6,
    __ubuf__ half *mem9,
    int repeat_times) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_bool pat_all_b16 = pset_b16(PAT_ALL);

        vector_f32 vec_1;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f16 vec_5;
        vector_f16 vec_7;
        vector_f16 vec_8;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
            vlds(vec_1, mem0, 64 * i, NORM);
            vlds(vec_3, mem2, 64 * i, NORM);
            vadd(vec_4, vec_1, vec_3, pat_all_b32);
            vcvt(vec_5, vec_4, pat_all_b32, ROUND_R, RS_DISABLE, PART_EVEN);
            vlds(vec_7, mem6, 128 * i, NORM);
            vadd(vec_8, vec_5, vec_7, pat_all_b16);
            vsts(vec_8, mem9, 128 * i, NORM_B16, pat_all_b16);
        }
    }
}

extern "C" __global__ __aicore__ void tadd_tcvt_tadd(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ input1,
    __gm__ half* __restrict__ input2,
    __gm__ half* __restrict__ output0) {
    __ubuf__ float *ub_data_0_addr_0 = (__ubuf__ float*)get_imm(0x00000);
    __ubuf__ float *ub_data_1_addr_0 = (__ubuf__ float*)get_imm(0x04000);
    __ubuf__ half *ub_data_2_addr_0 = (__ubuf__ half*)get_imm(0x08000);
    __ubuf__ half *ub_data_3_addr_0 = (__ubuf__ half*)get_imm(0x0c000);

    int repeat_times = 16;

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_0_addr_0, (__gm__ float*)input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_1_addr_0, (__gm__ float*)input1,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ half*)ub_data_2_addr_0, (__gm__ half*)input2,
                             0, 1, repeat_times * 128 * 2, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    tadd_tcvt_tadd_simd_ub(
        ub_data_0_addr_0,
        ub_data_1_addr_0,
        ub_data_2_addr_0,
        ub_data_3_addr_0,
        repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2((__gm__ half*)output0, (__ubuf__ half*)ub_data_3_addr_0,
                             0, 1, repeat_times * 128 * 2, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}
