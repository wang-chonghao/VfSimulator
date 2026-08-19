# Direct VCVT

This variant changes only the conversion instruction:

```text
vmulscvt(exp, 1.0) -> vcvt(exp)
```

`vexpdif` and all other instructions remain unchanged. The sources are
independent implementations and do not use compile-time switches.

| Unroll | VF start | VF end | VF cycles | Total tick | Precision |
|---:|---:|---:|---:|---:|---|
| 1 | 2618 | 3233 | 615 | 4075 | PASS |
| 2 | 2624 | 3246 | 622 | 4087 | PASS |
| 4 | 2639 | 3257 | 618 | 4103 | PASS |

The raw `nz_out` SHA-256 is identical for U1, U2, and U4:
`0e29ddd8ac3abf18f6486befad32cb1b471f5c2d0f3cacde02d25b6b8d208b02`.
