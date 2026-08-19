# Macro Instructions With IR-Aligned Output Stream

This case keeps the macro-style compute instruction shape where possible:

- `vmuls`
- `vexpdif`
- `vmulscvt`
- `vpack`
- `vsstb`

The `nz_out` write stream is changed to the IR-aligned single-stream form:

```cpp
__ubuf__ half *nz_ptr = (__ubuf__ half *)nz_out;
vsstb(cvt, nz_ptr, ((129 << 16) | 1), pred_b16_vl64, POST_UPDATE);
```

## Result

- Build flag: `-mllvm -cce-aicore-vec-misched=0`
- Total tick: `4178`
- VF start: `2625`
- VF end: `3338`
- VF cycle: `713`
- VF instr_num: `0x29`
- Host check: `PASS` for `new_global_max` and `new_global_sum`

## Notes

`vmulscvt` on this CANN compiler only accepts `vector_f16` destination, not
`vector_bf16`. Therefore this case does not use the IR-aligned BF16 `x_exp`
golden check. The raw `nz_out` buffer is written through `vsstb` and should not
be interpreted as a simple row-major BF16 array.
