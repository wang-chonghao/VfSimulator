#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def main() -> None:
    p = argparse.ArgumentParser(description='Calibrate forwarding matrix using existing suite')
    p.add_argument('--generate-missing', action='store_true', help='Generate and run unconfigured pair cases too')
    p.add_argument('--msprof-root', default='/home/lenovo/msprof_run')
    p.add_argument('--fwd-json', default='/mnt/d/VfSimulator/configs/forwarding.json')
    p.add_argument('--csv-out', default='/mnt/d/VfSimulator/results/forwarding_param_suite_compare.csv')
    p.add_argument('--unconfigured-csv-out', default='/mnt/d/VfSimulator/results/forwarding_unconfigured_measured.csv')
    a = p.parse_args()

    run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && python3 ascend_runner/forwarding_param_suite/generate_cases.py"')
    run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && bash ascend_runner/forwarding_param_suite/run_suite.sh"')

    run(
        'wsl -d Ubuntu -- bash -lc '
        f'"cd /mnt/d/VfSimulator && python3 ascend_runner/forwarding_param_suite/extract_forwarding_params.py '
        f'--msprof-root {a.msprof_root} --fwd-json {a.fwd_json} --csv-out {a.csv_out}"'
    )

    if a.generate_missing:
        run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && python3 ascend_runner/forwarding_param_suite/generate_unconfigured_cases.py"')
        run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && bash tools/run_forwarding_case_list.sh tools/fwd_unconfigured_cases.txt"')
        run(
            'wsl -d Ubuntu -- bash -lc '
            f'"cd /mnt/d/VfSimulator && python3 ascend_runner/forwarding_param_suite/extract_unconfigured_forwarding.py '
            f'--msprof-root {a.msprof_root} --csv-out {a.unconfigured_csv_out}"'
        )


if __name__ == '__main__':
    main()

