#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def main() -> None:
    p = argparse.ArgumentParser(description='Calibrate II matrix using existing suite')
    p.add_argument('--msprof-root', default='/home/lenovo/msprof_run')
    p.add_argument('--ii-json', default='/mnt/d/VfSimulator/configs/InitiationInterval.json')
    p.add_argument('--csv-out', default='/mnt/d/VfSimulator/results/ii_param_suite_compare.csv')
    a = p.parse_args()

    run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && python3 ascend_runner/ii_param_suite/generate_cases.py"')
    run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && bash tools/run_ii_case_list.sh tools/ii_all_cases.txt"')
    run(
        'wsl -d Ubuntu -- bash -lc '
        f'"cd /mnt/d/VfSimulator && python3 ascend_runner/ii_param_suite/extract_ii_params.py '
        f'--msprof-root {a.msprof_root} --ii-json {a.ii_json} --csv-out {a.csv_out}"'
    )


if __name__ == '__main__':
    main()
