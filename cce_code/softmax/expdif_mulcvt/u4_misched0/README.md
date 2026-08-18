# Macro Instructions With IR-Aligned Output Stream, U4

This case is the 4-way unrolled version of `macro_instr_ir_layout/u1_misched0`.
It keeps the current IR-aligned single `nz_ptr` stream layout:

```cpp
vsstb(cvt0, nz_ptr, ((129 << 16) | 1), pred_b16_vl64, POST_UPDATE);
vsstb(cvt1, nz_ptr, ((129 << 16) | 1), pred_b16_vl64, POST_UPDATE);
vsstb(cvt2, nz_ptr, ((129 << 16) | 1), pred_b16_vl64, POST_UPDATE);
vsstb(cvt3, nz_ptr, ((129 << 16) | 1), pred_b16_vl64, POST_UPDATE);
```

It does not try to reproduce the macro four-stream layout.

The four unrolled rows use independent sum accumulators:

```cpp
vadd(sum_even, exp0, sum_even, ...);
vadd(sum_odd, exp1, sum_odd, ...);
vadd(sum_even_1, exp2, sum_even_1, ...);
vadd(sum_odd_1, exp3, sum_odd_1, ...);
```

This avoids extending the dependency chain through one reused `sum` register.

## Result

- Build flag: `-mllvm -cce-aicore-vec-misched=0`
- Total tick: `4079`
- VF start: `2638`
- VF end: `3232`
- VF cycle: `594`
- VF instr_num: `0x44`
- Host check: `PASS` for `new_global_max` and `new_global_sum`
