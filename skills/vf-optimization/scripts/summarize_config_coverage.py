#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(r'd:/VfSimulator')


def count_mismatch(csv_path: Path, has_match: bool = True) -> tuple[int, int]:
    if not csv_path.exists():
        return 0, 0
    rows = list(csv.DictReader(csv_path.open('r', encoding='utf-8')))
    if not has_match:
        return len(rows), 0
    bad = [r for r in rows if str(r.get('match', '')).lower() != 'true']
    return len(rows), len(bad)


def main() -> None:
    targets = [
        ('ISA', ROOT/'results'/'single_op_param_suite_compare.csv', True),
        ('Forwarding', ROOT/'results'/'forwarding_param_suite_compare.csv', True),
        ('Forwarding-Unconfigured', ROOT/'results'/'forwarding_unconfigured_measured.csv', False),
        ('II', ROOT/'results'/'ii_param_suite_compare.csv', True),
        ('DispatchEXU', ROOT/'results'/'dispatch_exu_summary.csv', False),
    ]
    for name, p, has_match in targets:
        total, bad = count_mismatch(p, has_match)
        if has_match:
            print(f'{name}: total={total}, mismatch={bad}, file={p}')
        else:
            print(f'{name}: total={total}, file={p}')


if __name__ == '__main__':
    main()
