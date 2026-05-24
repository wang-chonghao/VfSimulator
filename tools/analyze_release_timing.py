import argparse
import re
from pathlib import Path


def parse_idu(path: Path):
    dispatch = []
    blocks = {}
    for line in path.read_text(errors="ignore").splitlines():
        if "instr send to OOO" in line:
            cyc = re.search(r"@(\d+)", line)
            iid = re.search(r"instr.id=(\d+)", line)
            op = re.search(r"instr.name=([^,]+)", line)
            vreg = re.search(r"vreg:(\d+)", line)
            if cyc and iid and op and vreg:
                dispatch.append(
                    {
                        "cycle": int(cyc.group(1)),
                        "id": int(iid.group(1)),
                        "op": op.group(1),
                        "vreg": int(vreg.group(1)),
                    }
                )
        if "IDU_BLOCK" in line and "REASON:" in line:
            reason = line.split("REASON:", 1)[1].strip()
            blocks[reason] = blocks.get(reason, 0) + 1
    return dispatch, blocks


def parse_instr_cycles(path: Path):
    out = {}
    for line in path.read_text(errors="ignore").splitlines():
        c = re.search(r"\[(\d+)\]", line)
        i = re.search(r"\(ID:\s*(\d+)\)", line)
        if c and i:
            out[int(i.group(1))] = int(c.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--head", type=int, default=80)
    args = ap.parse_args()

    d = Path(args.dump_dir)
    idu = d / "core0.veccore0.rvec.IDU.dump"
    popped = d / "core0.veccore0.instr_popped_log.dump"
    done = d / "core0.veccore0.instr_log.dump"

    dispatch, blocks = parse_idu(idu)
    start_by_id = parse_instr_cycles(popped)
    done_by_id = parse_instr_cycles(done)

    print(f"[INFO] case={d.name} dispatch={len(dispatch)}")
    print("[INFO] top block reasons:")
    for k, v in sorted(blocks.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  - {k}: {v}")

    print("\n[INFO] first dispatch records:")
    for row in dispatch[: args.head]:
        iid = row["id"]
        s = start_by_id.get(iid)
        e = done_by_id.get(iid)
        print(
            f"  cyc={row['cycle']:>6} id={iid:>6} op={row['op']:<10} "
            f"vreg={row['vreg']:>3} start={str(s):>6} done={str(e):>6}"
        )

    print("\n[INFO] vreg up-jump events:")
    prev = None
    for row in dispatch:
        if prev is not None and row["vreg"] > prev["vreg"]:
            print(
                f"  window=({prev['cycle']}->{row['cycle']}) "
                f"vreg:{prev['vreg']}->{row['vreg']} at id={row['id']} op={row['op']}"
            )
        prev = row


if __name__ == "__main__":
    main()
