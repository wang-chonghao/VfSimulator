#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

LINE_RE = re.compile(r"instr_name\s+(RV_[A-Z0-9_]+).*?exu_id:(\d+)")


def main() -> None:
    p = argparse.ArgumentParser(description='Infer dispatch capability from EXU dumps')
    p.add_argument('--msprof-root', default='/home/lenovo/msprof_run')
    p.add_argument('--op-filter', default='RV_', help='prefix filter, e.g. RV_VC')
    p.add_argument('--csv-out', default='/mnt/d/VfSimulator/results/dispatch_exu_summary.csv')
    a = p.parse_args()

    root = Path(a.msprof_root)
    exu_map: dict[str, set[int]] = defaultdict(set)

    for dump in root.glob('*_native_simexec/core0.veccore0.rvec.EXU.dump'):
        txt = dump.read_text(encoding='utf-8', errors='ignore')
        for line in txt.splitlines():
            m = LINE_RE.search(line)
            if not m:
                continue
            op = m.group(1)
            if not op.startswith(a.op_filter):
                continue
            exu_map[op].add(int(m.group(2)))

    rows = []
    for op in sorted(exu_map):
        exus = sorted(exu_map[op])
        if 0 in exus and 1 in exus:
            cap = 'EXU01'
        elif exus == [0]:
            cap = 'EXU0_ONLY'
        else:
            cap = f'UNKNOWN({"/".join(map(str, exus))})'
        rows.append({'op': op, 'exu_ids': '/'.join(map(str, exus)), 'dispatch_exu': cap})

    out = Path(a.csv_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['op', 'exu_ids', 'dispatch_exu'])
        w.writeheader()
        w.writerows(rows)

    print(f'wrote {len(rows)} rows to {out}')


if __name__ == '__main__':
    main()
