#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void src_fanout_probe_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_times) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_1;
        vector_f32 vec_2;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f32 vec_5;
        vector_f32 vec_6;
        vector_f32 vec_7;
        vector_f32 vec_8;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
            vlds(vec_1, memA, 64 * i, NORM);
            vadds(vec_2, vec_1, 1.0f, pat_all_b32);
            vadds(vec_3, vec_1, 1.0f, pat_all_b32);
            vadds(vec_4, vec_1, 1.0f, pat_all_b32);
            vadds(vec_5, vec_1, 1.0f, pat_all_b32);
            vadd(vec_6, vec_2, vec_3, pat_all_b32);
            vadd(vec_7, vec_4, vec_5, pat_all_b32);
            vadd(vec_8, vec_6, vec_7, pat_all_b32);
            vsts(vec_8, memB, 64 * i, NORM_B32, pat_all_b32);
        }
    }
}

extern "C" __global__ __aicore__ void src_fanout_probe(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ output0) {
    __ubuf__ float *ub_data_0_addr_0 = (__ubuf__ float*)get_imm(0x00000);
    __ubuf__ float *ub_data_1_addr_0 = (__ubuf__ float*)get_imm(0x04000);

    int repeat_times = 64;

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_0_addr_0, (__gm__ float*)input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    src_fanout_probe_simd_ub(
        ub_data_0_addr_0,
        ub_data_1_addr_0,
        repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2((__gm__ float*)output0, (__ubuf__ float*)ub_data_1_addr_0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}
