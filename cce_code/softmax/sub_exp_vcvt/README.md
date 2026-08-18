# Split VEXPDIF And Direct VCVT

This variant applies both decompositions:

```text
vexpdif(x, max)       -> vsub(x, max) + vexp
vmulscvt(exp, 1.0)    -> vcvt(exp)
```

All other instructions remain unchanged. The sources are independent
implementations and do not use compile-time switches.

| Unroll | VF start | VF end | VF cycles | Total tick | Precision |
|---:|---:|---:|---:|---:|---|
| 1 | 2620 | 3306 | 686 | 4148 | PASS |
| 2 | 2617 | 3298 | 681 | 4139 | PASS |
| 4 | 2638 | 3320 | 682 | 4166 | PASS |

The raw `nz_out` SHA-256 is identical for U1, U2, and U4:
`0e29ddd8ac3abf18f6486befad32cb1b471f5c2d0f3cacde02d25b6b8d208b02`.
