| case | baseline(consumer-done) | start+5 | start+5+queue_level2 | start+5+queue_level3 | queue_level4+vregpass (shq=inf exq=inf) | queue_level4+vregpass (shq=58 exq=26) |
|---|---:|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 12.33% | 12.33% | 10.76% | 9.19% | 12.11% | 12.11% |
| gelu_poly_i64_u1 | 15.05% | 15.05% | 12.65% | 11.05% | 13.78% | 13.78% |
| gelu_poly_i96_u1 | 17.10% | NA | 14.43% | 12.83% | 15.59% | 15.59% |
| gelu_i16_u1 | 8.02% | 8.02% | 11.76% | 8.02% | 1.07% | 1.07% |
| online_update_i64_u1 | 13.09% | 13.09% | 11.96% | 10.84% | 2.93% | 2.93% |
| probe_src_fanout | 0.70% | 0.70% | 1.05% | 1.05% | 1.05% | 1.05% |
| probe_branch_live_range | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% |
| probe_store_capture_reuse | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% |
| vadds_longchain_i16_1x512 | 0.14% | 0.14% | 0.14% | 0.07% | 0.14% | 1.44% |
| vadds_longchain_i64_8x64 | 2.17% | 2.17% | 2.17% | 1.13% | 2.17% | 6.64% |
| vadds_longchain_i128_128x4 | 1.39% | NA | 1.39% | 1.39% | 1.39% | 1.39% |
| vexp_longchain_i16_1x512 | 1.81% | 1.81% | 1.79% | 1.74% | 1.79% | 0.02% |
| vexp_longchain_i64_8x64 | NA | NA | NA | NA | NA | NA |
| vexp_longchain_i128_128x4 | NA | NA | NA | NA | NA | NA |
| gelu_poly_i96_u2 | 12.52% | 12.52% | 9.50% | 9.50% | 8.41% | 8.41% |
| gelu_poly_i96_u4 | 2.45% | 2.45% | 8.94% | 2.69% | 1.75% | 1.75% |
| gelu_poly_i96_u8 | 0.29% | 0.29% | 6.11% | 30.38% | 1.98% | 1.98% |
| silu_i16_u1 | 6.88% | 6.88% | 10.00% | 6.88% | 2.50% | 2.50% |
| swiglu_i16_u1 | 6.67% | 6.67% | 7.78% | 6.11% | 6.11% | 6.11% |
| silu_i64_u1 | 8.03% | 8.03% | 10.88% | 5.70% | 5.18% | 5.18% |
| silu_i96_u1 | 9.48% | 9.48% | 11.15% | 5.95% | 6.13% | 6.13% |
| swiglu_i64_u1 | 9.38% | 9.38% | 9.58% | 4.79% | 5.21% | 5.21% |
| swiglu_i96_u1 | 9.44% | 9.44% | 9.14% | 4.42% | 3.83% | 3.83% |

## Camodel and queue_level4+vregpass(shq=58 exq=26) Time

| case | loop block count | loop iteration count | per-loop chain length | camodel VF end | queue_level4+vregpass (shq=58 exq=26) VF end |
|---|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 1 | 16 | NA | 446 | 392 |
| gelu_poly_i64_u1 | 1 | 64 | NA | 1502 | 1295 |
| gelu_poly_i96_u1 | 1 | 96 | NA | 2245 | 1895 |
| gelu_i16_u1 | 1 | 16 | NA | 187 | 189 |
| online_update_i64_u1 | 1 | 64 | NA | 443 | 430 |
| probe_src_fanout | NA | NA | NA | 285 | 282 |
| probe_branch_live_range | NA | NA | NA | 221 | 218 |
| probe_store_capture_reuse | NA | NA | NA | 319 | 317 |
| vadds_longchain_i16_1x512 | 1 | 16 | 512 | 21823 | 22138 |
| vadds_longchain_i64_8x64 | 8 | 64 | 64 | 37281 | 39755 |
| vadds_longchain_i128_128x4 | 128 | 128 | 4 | 36641 | 36131 |
| vexp_longchain_i16_1x512 | 1 | 16 | 512 | 95492 | 95477 |
| vexp_longchain_i64_8x64 | 8 | 64 | 64 | NA | 163947 |
| vexp_longchain_i128_128x4 | 128 | 128 | 4 | NA | 134947 |
| gelu_poly_i96_u2 | 1 | 96 | NA | 2021 | 1851 |
| gelu_poly_i96_u4 | 1 | 96 | NA | 1712 | 1682 |
| gelu_poly_i96_u8 | 1 | 96 | NA | 1718 | 1684 |
| silu_i16_u1 | 1 | 16 | NA | 160 | 156 |
| swiglu_i16_u1 | 1 | 16 | NA | 180 | 169 |
| silu_i64_u1 | 1 | 64 | NA | 386 | 406 |
| silu_i96_u1 | 1 | 96 | NA | 538 | 571 |
| swiglu_i64_u1 | 1 | 64 | NA | 480 | 455 |
| swiglu_i96_u1 | 1 | 96 | NA | 678 | 652 |
