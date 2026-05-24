import re
from pathlib import Path

case = "reg_release_two_consumers"
path = Path(f"/home/lenovo/msprof_run/{case}_native_simexec/core0.veccore0.rvec.IDU.dump")
rows = []
for line in path.read_text(errors="ignore").splitlines():
    if "instr send to OOO" not in line:
        continue
    cyc = int(re.search(r"@(\d+)", line).group(1))
    name = re.search(r"instr.name=([^,]+)", line).group(1)
    iid = int(re.search(r"instr.id=(\d+)", line).group(1))
    vreg = int(re.search(r"vreg:(\d+)", line).group(1))
    rows.append((cyc, iid, name, vreg))

prev = None
for i, (cyc, iid, name, vreg) in enumerate(rows):
    if prev is not None and vreg > prev[3]:
        print(f"INC at idx={i}: cycle={cyc} id={iid} op={name} vreg={vreg} prev={prev[3]}")
        for j in range(max(0, i - 3), min(len(rows), i + 3)):
            c2, id2, n2, vr2 = rows[j]
            mark = ">>" if j == i else "  "
            print(f"{mark} idx={j} cyc={c2} id={id2} op={n2} vreg={vr2}")
        print("-" * 72)
    prev = (cyc, iid, name, vreg)
