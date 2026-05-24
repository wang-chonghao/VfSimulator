# DaVinci AIC V300 ISA User Guide for Vector Thread Extension


## SMEM_BAR
Please be note that all store instructions would be issued out of order. Thus for store instructions write the same UB address(including SCATTER instructions which contains the same destination address), programmer need to insert a MEM_BAR instruction between them.

### syntax
SMEM_BAR.type

---

### Description

.type={.VV_ALL .VST_VLD .VLD_VST .VST_VST .VS_ALL .VST_LD .VLD_ST .VST_ST .SV_ALL .ST_VLD .LD_VST .ST_VST}

This instruction blocks the UB access

- .VV_ALL
blocks the execution of vector load/store instructions untill all the vector load/store instructions have been completed.

- .VST_VLD
blocks the execution of vector load instructions untill all the vector store instructions have been completed.

- .VLD_VST
blocks the execution of vector store instructions untill all the vector load instructions have been completed.

- .VST_VST
blocks the execution of vector store instructions untill all the vector store instructions have been completed.

- .VS_ALL
blocks the execution of scalar load/store instructions untill all the vector load/store instructions have been completed.

- .VST_LD
blocks the execution of scalar load instructions untill all the vector store instructions have been completed.

- .VLD_ST
blocks the execution of scalar store instructions untill all the vector load instructions have been completed.

- .VST_ST
blocks the execution of scalar store instructions untill all the vector store instructions have been completed.

- .SV_ALL
blocks the execution of vector load/store instructions untill all the scalar load/store instructions have been completed.

- .ST_VLD
blocks the execution of vector load instructions untill all the scalar store instructions have been completed.

- .LD_VST
blocks the execution of vector store instructions untill all the scalar load instructions have been completed.

- .ST_VST
blocks the execution of vector store instructions untill all the scalar store instructions have been completed.


