# Macro Instructions With IR-Aligned Output Stream

All cases use the same softmax semantics and host golden. They are built with
`-mllvm -cce-aicore-vec-misched=0`.

## Original VEXPDIF And VMULSCVT

| Unroll | VF start | VF end | VF cycles | Total tick | Precision |
|---:|---:|---:|---:|---:|---|
| 1 | 2625 | 3338 | 713 | 4178 | PASS |
| 2 | 2623 | 3232 | 609 | 4073 | PASS |
| 4 | 2638 | 3232 | 594 | 4079 | PASS |

The U2 source and results are in `u2_misched0/`.

