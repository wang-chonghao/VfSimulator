#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

// Dense softmax tile:
// logical shape [B=1, H=1, M=8, N=1024].
// Each row computes softmax(qk_row) across all 1024 columns; no attention scale is applied.
__attribute__((always_inline)) inline [aicore] void attention_softmax_row1024_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB) {

    __VEC_SCOPE__ {
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_x;
        vector_f32 vec_blk_max;
        vector_f32 vec_blk_max_bcast;
        vector_f32 vec_row_max;
        vector_f32 vec_shift;
        vector_f32 vec_exp;
        vector_f32 vec_blk_sum;
        vector_f32 vec_blk_sum_bcast;
        vector_f32 vec_row_sum;
        vector_f32 vec_out;

        vdup(vec_row_max, -3.4028234663852886e38f, pat_all_b32, MODE_ZEROING);
        #pragma unroll(1)
        for (uint16_t blk = 0; blk < 16; ++blk) {
            vlds(vec_x, memA, 64 * blk, NORM);
            vcmax(vec_blk_max, vec_x, pat_all_b32, MODE_ZEROING);
            vdup(vec_blk_max_bcast, vec_blk_max, pat_all_b32, POS_LOWEST, MODE_ZEROING);
            vmax(vec_row_max, vec_row_max, vec_blk_max_bcast, pat_all_b32);
        }

        vdup(vec_row_sum, 0.0f, pat_all_b32, MODE_ZEROING);
        #pragma unroll(1)
        for (uint16_t blk = 0; blk < 16; ++blk) {
            vlds(vec_x, memA, 64 * blk, NORM);
            vsub(vec_shift, vec_x, vec_row_max, pat_all_b32);
            vexp(vec_exp, vec_shift, pat_all_b32);
            vcadd(vec_blk_sum, vec_exp, pat_all_b32, MODE_ZEROING);
            vdup(vec_blk_sum_bcast, vec_blk_sum, pat_all_b32, POS_LOWEST, MODE_ZEROING);
            vadd(vec_row_sum, vec_row_sum, vec_blk_sum_bcast, pat_all_b32);
        }

        #pragma unroll(1)
        for (uint16_t blk = 0; blk < 16; ++blk) {
            vlds(vec_x, memA, 64 * blk, NORM);
            vsub(vec_shift, vec_x, vec_row_max, pat_all_b32);
            vexp(vec_exp, vec_shift, pat_all_b32);
            vdiv(vec_out, vec_exp, vec_row_sum, pat_all_b32);
            vsts(vec_out, memB, 64 * blk, NORM_B32, pat_all_b32);
        }
    }
}

extern "C" __global__ __aicore__ void attention_softmax_kernel(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ input1,
    __gm__ float* __restrict__ output0)
{
    (void)input1;
    __ubuf__ float *ub_data_x_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_y_addr_0 = (__ubuf__ float*)get_imm(0x10000);

    constexpr int rows = 8;
    constexpr int cols = 1024;
    constexpr int row_bytes = cols * 4;

    for (int row = 0; row < rows; ++row) {
        int gm_offset = row * cols;
        copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)(input0 + gm_offset),
                                 0, 1, row_bytes, 0, 0, 0, 0, 0, 0);
        set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
        wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

        attention_softmax_row1024_simd_ub(ub_data_x_addr_0, ub_data_y_addr_0);

        pipe_barrier(PIPE_ALL);
        copy_ubuf_to_gm_align_v2((__gm__ float*)(output0 + gm_offset), (__ubuf__ float*)ub_data_y_addr_0,
                                 0, 1, row_bytes, 0, 0, 0);
        pipe_barrier(PIPE_ALL);
    }
}
