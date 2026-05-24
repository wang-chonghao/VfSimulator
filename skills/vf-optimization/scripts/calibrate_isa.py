#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess


def run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True)


def main() -> None:
    p = argparse.ArgumentParser(description='Calibrate ISA single-op params using existing suite')
    p.add_argument('--run-suite', action='store_true', help='Build/run single-op suite before extraction')
    p.add_argument('--msprof-root', default='/home/lenovo/msprof_run')
    p.add_argument('--isa-json', default='/mnt/d/VfSimulator/configs/isa.json')
    p.add_argument('--csv-out', default='/mnt/d/VfSimulator/results/single_op_param_suite_compare.csv')
    a = p.parse_args()

    if a.run_suite:
        run('wsl -d Ubuntu -- bash -lc "cd /mnt/d/VfSimulator && bash ascend_runner/single_op_param_suite/run_suite.sh"')

    run(
        'wsl -d Ubuntu -- bash -lc '
        f'"cd /mnt/d/VfSimulator && python3 ascend_runner/single_op_param_suite/extract_single_op_params.py '
        f'--msprof-root {a.msprof_root} --isa-json {a.isa_json} --csv-out {a.csv_out}"'
    )


if __name__ == '__main__':
    main()
