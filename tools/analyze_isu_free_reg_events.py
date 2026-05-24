import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


START_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\).*?([A-Z0-9_]+)")
ISU_FREE_RE = re.compile(
    r"\[(?P<cycle>\d+)\].*ISU_SCB_FREE_REG instr_name (?P<op>\S+) "
    r"instr_id (?P<id>\d+).*event:DEC_SRC reg_type:(?P<reg_type>\S+) "
    r"v_idx:(?P<v_idx>\d+) p_idx:(?P<p_idx>\d+)"
)
IDU_SEND_RE = re.compile(
    r"@(?P<cycle>\d+).*instr send to OOO: instr\.name=(?P<op>[^,]+).*?"
    r"instr\.id=(?P<id>\d+).*ooo=\(preg:\d+, vreg:(?P<vreg>\d+)\)"
)


def parse_instr_cycles(path: Path) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"\[(\d+)\].*\(ID:\s*(\d+)\)", line)
        if m:
            out[int(m.group(2))] = int(m.group(1))
    return out


def parse_idu_dispatch(path: Path) -> List[dict]:
    rows: List[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = IDU_SEND_RE.search(line)
        if not m:
            continue
        rows.append(
            {
                "cycle": int(m.group("cycle")),
                "id": int(m.group("id")),
                "op": m.group("op"),
                "vreg_free": int(m.group("vreg")),
            }
        )
    return rows


def parse_isu_free_events(path: Path) -> Dict[int, List[dict]]:
    events_by_id: Dict[int, List[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = ISU_FREE_RE.search(line)
        if not m:
            continue
        iid = int(m.group("id"))
        events_by_id[iid].append(
            {
                "cycle": int(m.group("cycle")),
                "op": m.group("op"),
                "reg_type": m.group("reg_type"),
                "v_idx": int(m.group("v_idx")),
                "p_idx": int(m.group("p_idx")),
            }
        )
    return events_by_id


def first_vreg_up_after(dispatch: List[dict], cycle_threshold: int) -> Optional[Tuple[int, int, int]]:
    prev = None
    for row in dispatch:
        if prev is not None and row["cycle"] >= cycle_threshold and row["vreg_free"] > prev["vreg_free"]:
            return row["cycle"], prev["vreg_free"], row["vreg_free"]
        prev = row
    return None


def summarize_by_op(records: List[dict]) -> List[str]:
    by_op: Dict[str, List[int]] = defaultdict(list)
    for rec in records:
        delta = rec.get("free_minus_start")
        if delta is not None:
            by_op[str(rec["op"])].append(int(delta))
    lines: List[str] = []
    for op in sorted(by_op):
        vals = sorted(by_op[op])
        cnt = Counter(vals)
        common = ", ".join(f"{k}:{v}" for k, v in sorted(cnt.items()))
        lines.append(f"{op}: count={len(vals)} deltas=[{common}]")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir", required=True, help="Directory containing CCE dump files")
    ap.add_argument("--head", type=int, default=40, help="Number of per-instruction rows to print")
    ap.add_argument(
        "--filter-op",
        default="",
        help="Only show instructions whose op contains this substring, e.g. RV_VDIV",
    )
    args = ap.parse_args()

    d = Path(args.dump_dir)
    popped = d / "core0.veccore0.instr_popped_log.dump"
    done = d / "core0.veccore0.instr_log.dump"
    idu = d / "core0.veccore0.rvec.IDU.dump"
    isu = d / "core0.veccore0.rvec.ISU.dump"

    missing = [str(p) for p in (popped, done, idu, isu) if not p.exists()]
    if missing:
        raise SystemExit(f"missing required files: {missing}")

    start_by_id = parse_instr_cycles(popped)
    done_by_id = parse_instr_cycles(done)
    dispatch = parse_idu_dispatch(idu)
    free_by_id = parse_isu_free_events(isu)

    records: List[dict] = []
    for iid, events in free_by_id.items():
        start = start_by_id.get(iid)
        done = done_by_id.get(iid)
        if not events:
            continue
        first = min(events, key=lambda e: e["cycle"])
        dispatch_row = next((r for r in dispatch if r["id"] == iid), None)
        release_proxy = first_vreg_up_after(dispatch, first["cycle"] + 2)
        rec = {
            "id": iid,
            "op": first["op"],
            "dispatch": dispatch_row["cycle"] if dispatch_row else None,
            "start": start,
            "done": done,
            "first_free": first["cycle"],
            "free_event_count": len(events),
            "free_minus_start": first["cycle"] - start if start is not None else None,
            "free_minus_done": first["cycle"] - done if done is not None else None,
            "release_proxy_cycle": release_proxy[0] if release_proxy else None,
            "release_proxy_minus_free": (release_proxy[0] - first["cycle"]) if release_proxy else None,
            "pregs": ",".join(str(e["p_idx"]) for e in events),
            "vregs": ",".join(str(e["v_idx"]) for e in events),
        }
        records.append(rec)

    records.sort(key=lambda r: (r["first_free"], r["id"]))

    if args.filter_op:
        records = [r for r in records if args.filter_op in str(r["op"])]

    print(f"[INFO] dump_dir={d}")
    print(f"[INFO] free_event_instr_count={len(records)}")
    print("[INFO] free-minus-start summary by op:")
    for line in summarize_by_op(records):
        print(f"  - {line}")

    print("\n[INFO] first rows:")
    for rec in records[: args.head]:
        print(
            "  "
            f"id={rec['id']:>5} op={rec['op']:<12} "
            f"dispatch={str(rec['dispatch']):>6} start={str(rec['start']):>6} "
            f"done={str(rec['done']):>6} free={str(rec['first_free']):>6} "
            f"free-start={str(rec['free_minus_start']):>4} "
            f"free-done={str(rec['free_minus_done']):>4} "
            f"idu_up={str(rec['release_proxy_cycle']):>6} "
            f"idu_up-free={str(rec['release_proxy_minus_free']):>4} "
            f"v={rec['vregs']} p={rec['pregs']}"
        )


if __name__ == "__main__":
    main()
