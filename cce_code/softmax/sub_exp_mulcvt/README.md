# Split VEXPDIF

This variant changes only the exponential-difference instruction:

```text
vexpdif(x, max) -> vsub(x, max) + vexp
```

`vmulscvt` and all other instructions remain unchanged. `vsub + vexp` is the
semantic decomposition of `exp(x - max)`; `vmul + vexp` would lose the
subtraction. The sources are independent implementations and do not use
compile-time switches.

| Unroll | VF start | VF end | VF cycles | Total tick | Precision |
|---:|---:|---:|---:|---:|---|
| 1 | 2621 | 3322 | 701 | 4165 | PASS |
| 2 | 2617 | 3285 | 668 | 4127 | PASS |
| 4 | 2638 | 3298 | 660 | 4146 | PASS |

The raw `nz_out` SHA-256 is identical for U1, U2, and U4:
`0e29ddd8ac3abf18f6486befad32cb1b471f5c2d0f3cacde02d25b6b8d208b02`.
