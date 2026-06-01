#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void gelu_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    __ubuf__ float *memC,
    __ubuf__ float *memD,
    __ubuf__ float *memE,
    __ubuf__ float *memF,
    __ubuf__ float *memG,
    __ubuf__ float *memH,
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
        vector_f32 vec_9;
        vector_f32 vec_10;
        vector_f32 vec_11;
        vector_f32 vec_12;
        for (uint16_t i=0; i< uint16_t(repeat_times); ++i) {
            vlds(vec_1, memA, 64*i, NORM);
            vlds(vec_2, memB, 64*i, NORM);
            vmax(vec_3, vec_1, vec_2, pat_all_b32);
            vsts(vec_3, memE, 64*i, NORM_B32, pat_all_b32);
            //vlds(vec_4, memD, 64*i, NORM);
            vsub(vec_4, vec_1, vec_3, pat_all_b32);
            vexp(vec_5, vec_4, pat_all_b32);
            vsts(vec_5, memF, 64*i, NORM_B32, pat_all_b32);
            vsub(vec_6, vec_2, vec_3, pat_all_b32);
            vexp(vec_7, vec_6, pat_all_b32);
            vsts(vec_7, memG, 64*i, NORM_B32, pat_all_b32);
            vlds(vec_8, memC, 64*i, NORM);
            vmul(vec_9, vec_5, vec_8, pat_all_b32);
            vlds(vec_10, memD, 64*i, NORM);
            vmul(vec_11, vec_10, vec_7, pat_all_b32);
            vadd(vec_12, vec_9, vec_11, pat_all_b32);
            vsts(vec_12, memH, 64*i, NORM_B32, pat_all_b32);
        }
    }    
}

extern "C" __global__ __aicore__ void foo_add(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_input2,
    __gm__ float* __restrict__ for_loop0_input3,
    __gm__ float* __restrict__ for_loop0_output0,
    __gm__ float* __restrict__ for_loop0_output1,
    __gm__ float* __restrict__ for_loop0_output2,
    __gm__ float* __restrict__ for_loop0_output3)
{
    __ubuf__ float *ub_data_0_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_1_addr_0 = (__ubuf__ float*)get_imm(0x4000);
    __ubuf__ float *ub_data_2_addr_0 = (__ubuf__ float*)get_imm(0x8000);
    __ubuf__ float *ub_data_3_addr_0 = (__ubuf__ float*)get_imm(0x12000);
    __ubuf__ float *ub_data_4_addr_0 = (__ubuf__ float*)get_imm(0x16000);
    __ubuf__ float *ub_data_5_addr_0 = (__ubuf__ float*)get_imm(0x20000);
    __ubuf__ float *ub_data_6_addr_0 = (__ubuf__ float*)get_imm(0x24000);
    __ubuf__ float *ub_data_7_addr_0 = (__ubuf__ float*)get_imm(0x28000);
    int repeat_times = 64;
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_0_addr_0, (__gm__ float*)for_loop0_input0,
                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_1_addr_0, (__gm__ float*)for_loop0_input1,
                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_2_addr_0, (__gm__ float*)for_loop0_input2,
                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_3_addr_0, (__gm__ float*)for_loop0_input3,
                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    gelu_simd_ub(ub_data_0_addr_0, ub_data_1_addr_0, ub_data_2_addr_0, ub_data_3_addr_0, 
            ub_data_4_addr_0, ub_data_5_addr_0,  ub_data_6_addr_0, ub_data_7_addr_0, repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_4_addr_0,
        0, 1, 16*1024, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}
   
