#!/usr/bin/env python3
"""Parse CAModel VF timing from a native_simexec run directory.

Metric: last VF end in core0.veccore0.instr_log.dump minus first VF start in
core0.veccore0.instr_popped_log.dump.
"""
import argparse
import json
import re
from pathlib import Path


def ints(line):
    return [int(x) for x in re.findall(r"\b\d+\b", line)]


def parse(run_dir: Path):
    popped_path = run_dir / "core0.veccore0.instr_popped_log.dump"
    log_path = run_dir / "core0.veccore0.instr_log.dump"
    if not popped_path.exists():
        raise FileNotFoundError(popped_path)
    if not log_path.exists():
        raise FileNotFoundError(log_path)

    starts = []
    for line in popped_path.read_text(errors="ignore").splitlines():
        if "VF" in line or "vf" in line:
            nums = ints(line)
            if nums:
                starts.append(nums[0])

    ends = []
    execute_times = []
    instr_counts = []
    for line in log_path.read_text(errors="ignore").splitlines():
        if "vf_execute_time" not in line:
            continue
        nums = ints(line)
        if nums:
            ends.append(nums[0])
            execute_times.append(nums[-1])
        m = re.search(r"instr_num:\s*0x([0-9a-fA-F]+)", line)
        if m:
            instr_counts.append(int(m.group(1), 16))

    if not starts:
        raise ValueError("No VF starts found in instr_popped log")
    if not ends:
        raise ValueError("No vf_execute_time entries found in instr log")

    first_start = min(starts)
    last_end = max(ends)
    return {
        "run_dir": str(run_dir),
        "first_vf_start": first_start,
        "last_vf_end": last_end,
        "vf_total_cycles": last_end - first_start,
        "vf_count": len(ends),
        "popped_starts": starts,
        "vf_end_cycles": ends,
        "vf_execute_times": execute_times,
        "vf_instr_counts": instr_counts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    data = parse(args.run_dir)
    print(json.dumps(data, indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()
