import re
from pathlib import Path

case = "reg_release_two_consumers"
path = Path(f"/home/lenovo/msprof_run/{case}_native_simexec/core0.veccore0.rvec.IDU.dump")
cnt = 0
for line in path.read_text(errors="ignore").splitlines():
    if "instr send to OOO" not in line:
        continue
    cyc = re.search(r"@(\d+)", line)
    name = re.search(r"instr.name=([^,]+)", line)
    iid = re.search(r"instr.id=(\d+)", line)
    vreg = re.search(r"vreg:(\d+)", line)
    print(
        f"cycle={cyc.group(1) if cyc else '?'} id={iid.group(1) if iid else '?'} "
        f"op={name.group(1) if name else '?'} vreg={vreg.group(1) if vreg else '?'}"
    )
    cnt += 1
    if cnt >= 60:
        break
