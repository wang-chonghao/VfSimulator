import json
import re
from pathlib import Path


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = int((len(sorted_vals) - 1) * p)
    return sorted_vals[idx]


def parse_case(base: Path, case: str):
    path = base / f"{case}_native_simexec" / "core0.veccore0.rvec.IDU.dump"
    lines = path.read_text(errors="ignore").splitlines()
    vregs = []
    reasons = {}
    dispatch_count = 0

    for line in lines:
        if "instr send to OOO" in line:
            dispatch_count += 1
            m = re.search(r"vreg:(\d+)", line)
            if m:
                vregs.append(int(m.group(1)))
        if "IDU_BLOCK" in line and "REASON:" in line:
            reason = line.split("REASON:", 1)[1].strip()
            reasons[reason] = reasons.get(reason, 0) + 1

    sv = sorted(vregs)
    return {
        "case": case,
        "dispatch_count": dispatch_count,
        "vreg_min": min(vregs) if vregs else None,
        "vreg_p01": percentile(sv, 0.01),
        "vreg_p50": percentile(sv, 0.50),
        "vreg_p99": percentile(sv, 0.99),
        "top_block_reasons": sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:8],
    }


def main():
    base = Path("/home/lenovo/msprof_run")
    cases = [
        "reg_release_single_consumer",
        "reg_release_two_consumers",
        "reg_release_overwrite_chain",
        "reg_release_longlat_mix",
    ]
    for case in cases:
        print(json.dumps(parse_case(base, case), ensure_ascii=False))


if __name__ == "__main__":
    main()
