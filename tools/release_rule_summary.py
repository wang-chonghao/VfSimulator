import re
from pathlib import Path


CASE_CFG = {
    "reg_release_e1_single_fast": {"producer": "RV_VADDS", "consumer": "RV_VEXP"},
    "reg_release_e2_dual_slowvdiv": {"producer": "RV_VADDS", "consumer": "RV_VDIV"},
    "reg_release_e3_gap0": {"producer": "RV_VADDS", "consumer": "RV_VDIV"},
    "reg_release_e3_gap8": {"producer": "RV_VADDS", "consumer": "RV_VDIV"},
    "reg_release_e4_store_only": {"producer": "RV_VADDS", "consumer": "RV_VST"},
    "reg_release_e4_compute_then_store": {"producer": "RV_VADDS", "consumer": "RV_VMULS"},
    "reg_release_e5_low_single": {"producer": "RV_VADDS", "consumer": "RV_VEXP"},
    "reg_release_e5_low_dual": {"producer": "RV_VADDS", "consumer": "RV_VDIV"},
    "reg_release_e6_highlive_fast": {"producer": "RV_VADDS", "consumer": "RV_VMULS"},
    "reg_release_e6_highlive_slow": {"producer": "RV_VADDS", "consumer": "RV_VEXP"},
}


def parse_dispatch(idu_path: Path):
    rows = []
    for line in idu_path.read_text(errors="ignore").splitlines():
        if "instr send to OOO" not in line:
            continue
        cyc = re.search(r"@(\d+)", line)
        iid = re.search(r"instr.id=(\d+)", line)
        op = re.search(r"instr.name=([^,]+)", line)
        vreg = re.search(r"vreg:(\d+)", line)
        if not (cyc and iid and op and vreg):
            continue
        rows.append(
            {
                "cycle": int(cyc.group(1)),
                "id": int(iid.group(1)),
                "op": op.group(1),
                "vreg": int(vreg.group(1)),
            }
        )
    return rows


def parse_cycle_file(path: Path):
    d = {}
    for line in path.read_text(errors="ignore").splitlines():
        c = re.search(r"\[(\d+)\]", line)
        i = re.search(r"\(ID:\s*(\d+)\)", line)
        if c and i:
            d[int(i.group(1))] = int(c.group(1))
    return d


def first_id(rows, op):
    for r in rows:
        if r["op"] == op:
            return r["id"], r["cycle"]
    return None, None


def first_jump_after(rows, after_cycle):
    prev = None
    for r in rows:
        if prev is not None and r["vreg"] > prev["vreg"] and prev["cycle"] >= after_cycle:
            return r["cycle"], prev["vreg"], r["vreg"], r["op"], r["id"]
        prev = r
    return None


def main():
    base = Path("/home/lenovo/msprof_run")
    print(
        "case,prod_id,prod_dispatch,cons_id,cons_dispatch,cons_start,cons_done,"
        "first_jump_after_cons_start,first_jump_after_cons_done"
    )
    for case, cfg in CASE_CFG.items():
        run_dir = base / f"{case}_native_simexec"
        rows = parse_dispatch(run_dir / "core0.veccore0.rvec.IDU.dump")
        starts = parse_cycle_file(run_dir / "core0.veccore0.instr_popped_log.dump")
        dones = parse_cycle_file(run_dir / "core0.veccore0.instr_log.dump")
        pid, pdispatch = first_id(rows, cfg["producer"])
        cid, cdispatch = first_id(rows, cfg["consumer"])
        cstart = starts.get(cid)
        cdone = dones.get(cid)
        j1 = first_jump_after(rows, cstart if cstart is not None else -1)
        j2 = first_jump_after(rows, cdone if cdone is not None else -1)
        print(
            f"{case},{pid},{pdispatch},{cid},{cdispatch},{cstart},{cdone},"
            f"{j1[0] if j1 else ''},{j2[0] if j2 else ''}"
        )


if __name__ == "__main__":
    main()
