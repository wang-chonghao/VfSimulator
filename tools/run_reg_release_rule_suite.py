import argparse
import csv
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CaseSpec:
    name: str
    dsl_rel: str
    kernel: str
    producer_op: str
    consumer_ops: Tuple[str, ...]
    num_inputs: int = 2
    num_outputs: int = 1
    total_elems: int = 6144


CASE_MATRIX: Dict[str, CaseSpec] = {
    "reg_release_single_consumer": CaseSpec(
        name="reg_release_single_consumer",
        dsl_rel="cce_code/reg_release_probe/reg_release_single_consumer.dsl",
        kernel="reg_release_single_consumer",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VST",),
        num_inputs=1,
        num_outputs=1,
    ),
    "reg_release_two_consumers": CaseSpec(
        name="reg_release_two_consumers",
        dsl_rel="cce_code/reg_release_probe/reg_release_two_consumers.dsl",
        kernel="reg_release_two_consumers",
        producer_op="RV_VLD",
        consumer_ops=("RV_VADD",),
        num_inputs=1,
        num_outputs=1,
    ),
    "reg_release_overwrite_chain": CaseSpec(
        name="reg_release_overwrite_chain",
        dsl_rel="cce_code/reg_release_probe/reg_release_overwrite_chain.dsl",
        kernel="reg_release_overwrite_chain",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VST",),
        num_inputs=1,
        num_outputs=1,
    ),
    "reg_release_e1_single_fast": CaseSpec(
        name="reg_release_e1_single_fast",
        dsl_rel="cce_code/reg_release_probe/reg_release_e1_single_fast.dsl",
        kernel="reg_release_e1_single_fast",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VEXP",),
    ),
    "reg_release_e2_dual_slowvdiv": CaseSpec(
        name="reg_release_e2_dual_slowvdiv",
        dsl_rel="cce_code/reg_release_probe/reg_release_e2_dual_slowvdiv.dsl",
        kernel="reg_release_e2_dual_slowvdiv",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VMULS", "RV_VDIV"),
    ),
    "reg_release_e3_gap0": CaseSpec(
        name="reg_release_e3_gap0",
        dsl_rel="cce_code/reg_release_probe/reg_release_e3_gap0.dsl",
        kernel="reg_release_e3_gap0",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VDIV",),
    ),
    "reg_release_e3_gap8": CaseSpec(
        name="reg_release_e3_gap8",
        dsl_rel="cce_code/reg_release_probe/reg_release_e3_gap8.dsl",
        kernel="reg_release_e3_gap8",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VDIV",),
    ),
    "reg_release_e4_store_only": CaseSpec(
        name="reg_release_e4_store_only",
        dsl_rel="cce_code/reg_release_probe/reg_release_e4_store_only.dsl",
        kernel="reg_release_e4_store_only",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VST",),
    ),
    "reg_release_e4_compute_then_store": CaseSpec(
        name="reg_release_e4_compute_then_store",
        dsl_rel="cce_code/reg_release_probe/reg_release_e4_compute_then_store.dsl",
        kernel="reg_release_e4_compute_then_store",
        producer_op="RV_VADDS",
        consumer_ops=("RV_VMULS", "RV_VST"),
    ),
    "reg_release_cross_iter_overwrite": CaseSpec(
        name="reg_release_cross_iter_overwrite",
        dsl_rel="cce_code/reg_release_probe/reg_release_cross_iter_overwrite.dsl",
        kernel="reg_release_cross_iter_overwrite",
        producer_op="RV_VLD",
        consumer_ops=("RV_VADDS", "RV_VST"),
        num_inputs=1,
        num_outputs=1,
    ),
    "reg_release_loop_boundary_seal": CaseSpec(
        name="reg_release_loop_boundary_seal",
        dsl_rel="cce_code/reg_release_probe/reg_release_loop_boundary_seal.dsl",
        kernel="reg_release_loop_boundary_seal",
        producer_op="RV_VLD",
        consumer_ops=("RV_VADDS", "RV_VST"),
        num_inputs=1,
        num_outputs=1,
    ),
    "reg_release_dual_consumer_reordered": CaseSpec(
        name="reg_release_dual_consumer_reordered",
        dsl_rel="cce_code/reg_release_probe/reg_release_dual_consumer_reordered.dsl",
        kernel="reg_release_dual_consumer_reordered",
        producer_op="RV_VLD",
        consumer_ops=("RV_VDIV", "RV_VADDS"),
        num_inputs=2,
        num_outputs=1,
    ),
}

REQ_DUMPS = (
    "core0.veccore0.rvec.IDU.dump",
    "core0.veccore0.rvec.ISU.dump",
    "core0.veccore0.rvec.EXU.dump",
    "core0.veccore0.instr_popped_log.dump",
    "core0.veccore0.instr_log.dump",
)

IDU_SEND_RE = re.compile(
    r"@(?P<cycle>\d+).*instr send to OOO: instr\.name=(?P<op>[^,]+).*instr\.id=(?P<id>\d+).*ooo=\(preg:\d+, vreg:(?P<vreg>\d+)\)"
)
IDU_BLOCK_RE = re.compile(r"IDU_BLOCK.*REASON:OOO no avail phy vreg")
INSTR_CYCLE_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\)")


def parse_idu(idu_path: Path) -> Tuple[List[dict], int]:
    dispatch = []
    block_no_vreg = 0
    for line in idu_path.read_text(errors="ignore").splitlines():
        m = IDU_SEND_RE.search(line)
        if m:
            dispatch.append(
                {
                    "cycle": int(m.group("cycle")),
                    "id": int(m.group("id")),
                    "op": m.group("op"),
                    "vreg": int(m.group("vreg")),
                }
            )
        if IDU_BLOCK_RE.search(line):
            block_no_vreg += 1
    return dispatch, block_no_vreg


def parse_instr_cycles(path: Path) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for line in path.read_text(errors="ignore").splitlines():
        m = INSTR_CYCLE_RE.search(line)
        if m:
            out[int(m.group(2))] = int(m.group(1))
    return out


def first_id_by_op(dispatch: List[dict], op: str) -> Optional[int]:
    for row in dispatch:
        if row["op"] == op:
            return row["id"]
    return None


def last_consumer_id(dispatch: List[dict], consumer_ops: Tuple[str, ...], done_by_id: Dict[int, int]) -> Optional[int]:
    cands = [r["id"] for r in dispatch if r["op"] in consumer_ops and r["id"] in done_by_id]
    if not cands:
        return None
    return max(cands, key=lambda i: done_by_id[i])


def find_first_vreg_up_after(dispatch: List[dict], cycle_threshold: int) -> Optional[int]:
    prev = None
    for row in dispatch:
        if prev is not None and row["cycle"] >= cycle_threshold and row["vreg"] > prev["vreg"]:
            return row["cycle"]
        prev = row
    return None


def analyze_case_dump(case: CaseSpec, dump_dir: Path) -> dict:
    missing = [f for f in REQ_DUMPS if not (dump_dir / f).exists()]
    if missing:
        return {
            "case": case.name,
            "status": "missing_dump",
            "dump_dir": str(dump_dir),
            "missing_files": "|".join(missing),
        }

    dispatch, block_no_vreg = parse_idu(dump_dir / "core0.veccore0.rvec.IDU.dump")
    start_by_id = parse_instr_cycles(dump_dir / "core0.veccore0.instr_popped_log.dump")
    done_by_id = parse_instr_cycles(dump_dir / "core0.veccore0.instr_log.dump")

    prod_id = first_id_by_op(dispatch, case.producer_op)
    if prod_id is None:
        return {
            "case": case.name,
            "status": "producer_not_found",
            "dump_dir": str(dump_dir),
            "block_no_avail_phy_vreg": block_no_vreg,
        }

    cons_id = last_consumer_id(dispatch, case.consumer_ops, done_by_id)
    if cons_id is None:
        return {
            "case": case.name,
            "status": "consumer_not_found",
            "dump_dir": str(dump_dir),
            "producer_id": prod_id,
            "block_no_avail_phy_vreg": block_no_vreg,
        }

    prod_start = start_by_id.get(prod_id)
    prod_done = done_by_id.get(prod_id)
    cons_start = start_by_id.get(cons_id)
    cons_done = done_by_id.get(cons_id)
    if cons_done is None:
        return {
            "case": case.name,
            "status": "consumer_done_missing",
            "dump_dir": str(dump_dir),
            "producer_id": prod_id,
            "last_consumer_id": cons_id,
            "block_no_avail_phy_vreg": block_no_vreg,
        }

    release_proxy = find_first_vreg_up_after(dispatch, cons_done)
    delta_done = release_proxy - cons_done if release_proxy is not None else None
    delta_start = release_proxy - cons_start if (release_proxy is not None and cons_start is not None) else None

    return {
        "case": case.name,
        "status": "ok" if release_proxy is not None else "no_vreg_up_after_consumer_done",
        "dump_dir": str(dump_dir),
        "producer_op": case.producer_op,
        "producer_id": prod_id,
        "producer_start": prod_start,
        "producer_done": prod_done,
        "consumer_ops": "|".join(case.consumer_ops),
        "last_consumer_id": cons_id,
        "last_consumer_start": cons_start,
        "last_consumer_done": cons_done,
        "release_proxy_cycle": release_proxy,
        "delta_to_last_consumer_done": delta_done,
        "delta_to_last_consumer_start": delta_start,
        "block_no_avail_phy_vreg": block_no_vreg,
    }


def run_case(repo_win: Path, case: CaseSpec, output_root: Path, misched: int) -> Tuple[str, Path]:
    stem = f"{case.name}_misched{misched}"
    dsl_wsl = f"/mnt/d/VfSimulator/{case.dsl_rel}"
    repo_wsl = "/mnt/d/VfSimulator"
    out_case = output_root / case.name / "cce_dump"
    out_case.mkdir(parents=True, exist_ok=True)
    out_case_wsl = str(out_case).replace("\\", "/").replace("d:/", "/mnt/d/").replace("D:/", "/mnt/d/")

    build = f"{repo_wsl}/ascend_runner/build/{stem}_native_simexec"
    cce = f"{build}/{stem}.cce"
    aiv = f"{build}/{stem}_mix_aiv.o"
    mix = f"{build}/{stem}_mix.o"
    sim = f"{build}/{stem}_simexec"
    src = f"/home/lenovo/msprof_run/{stem}_native_simexec"
    ccec = "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/bin/ccec"
    lld = "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/bin/ld.lld"

    cmd = f"""
set -euo pipefail
echo "[DBG] repo_wsl={repo_wsl}"
echo "[DBG] dsl={dsl_wsl}"
echo "[DBG] build={build}"

bash "{repo_wsl}/ascend_runner/current/build_native_simexec.sh" "{dsl_wsl}" "{stem}"
"{ccec}" -g -std=c++17 -c -O2 "{cce}" -o "{aiv}" \
  -I/usr/include/c++/11 \
  -I/usr/include/aarch64-linux-gnu/c++/11 \
  --cce-aicore-arch=dav-c310-vec \
  --cce-aicore-only \
  -mllvm -cce-aicore-function-stack-size=16000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-jump-expand=true \
  -mllvm -cce-aicore-vec-misched={misched} \
  --cce-simd-vf-fusion=false
"{lld}" -Ttext=0 "{aiv}" -static -o "{mix}"
bash "{repo_wsl}/ascend_runner/current/run_native_simexec.sh" \
  "{sim}" "{mix}" "{case.kernel}" {case.num_inputs} {case.num_outputs} {case.total_elems}

mkdir -p "{out_case_wsl}"
cp -f "{src}/core0.veccore0.rvec.IDU.dump" "{out_case_wsl}/"
cp -f "{src}/core0.veccore0.rvec.ISU.dump" "{out_case_wsl}/"
cp -f "{src}/core0.veccore0.rvec.EXU.dump" "{out_case_wsl}/"
cp -f "{src}/core0.veccore0.instr_popped_log.dump" "{out_case_wsl}/"
cp -f "{src}/core0.veccore0.instr_log.dump" "{out_case_wsl}/"
"""
    cmd = cmd.replace("\r", "")
    proc = subprocess.run(
        ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", cmd],
        capture_output=True,
        text=True,
    )
    return (proc.stdout + "\n" + proc.stderr).strip(), out_case


def write_csv(rows: List[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    keys = set()
    for r in rows:
        keys.update(r.keys())
    fieldnames = sorted(keys)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run/analyze CCE register-release experiment suite.")
    ap.add_argument("--repo", default=r"d:\VfSimulator")
    ap.add_argument("--output-root", default=r"d:\VfSimulator\results\tmp_reg_release_rule_probe")
    ap.add_argument("--misched", type=int, default=0)
    ap.add_argument("--run", action="store_true", help="Build and execute cases in WSL.")
    ap.add_argument("--analyze-only", action="store_true", help="Only analyze existing dumps under output-root.")
    ap.add_argument("--cases", nargs="*", default=list(CASE_MATRIX.keys()))
    ap.add_argument("--summary-csv", default="summary.csv")
    args = ap.parse_args()

    repo = Path(args.repo)
    output_root = Path(args.output_root)
    selected = []
    for name in args.cases:
        if name not in CASE_MATRIX:
            raise ValueError(f"Unknown case: {name}")
        selected.append(CASE_MATRIX[name])

    rows: List[dict] = []
    for case in selected:
        dump_dir = output_root / case.name / "cce_dump"
        run_log = ""
        if args.run and not args.analyze_only:
            run_log, dump_dir = run_case(repo, case, output_root, args.misched)
        row = analyze_case_dump(case, dump_dir)
        if run_log:
            row["run_log_tail"] = run_log[-400:]
        rows.append(row)
        print(f"[{row.get('status','unknown')}] {case.name} dump={dump_dir}")

    summary_path = output_root / args.summary_csv
    write_csv(rows, summary_path)
    print(f"[DONE] wrote {summary_path}")


if __name__ == "__main__":
    main()
