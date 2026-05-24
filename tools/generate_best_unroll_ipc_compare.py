import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "results" / "three_ports_test"
BEST_JSON = RESULTS_ROOT / "three_ports_unroll_best_compare.json"
UNROLL_RUNS_ROOT = RESULTS_ROOT / "unroll_runs"
OUT_DIR = RESULTS_ROOT / "best_unroll_ipc_compare"
PLOT_SCRIPT = ROOT / "tools" / "plot_model_ipc_compare.py"


def main() -> None:
    data = json.loads(BEST_JSON.read_text(encoding="utf-8"))
    rows = data["rows"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    md_lines = [
        "# Best Unroll IPC Compare",
        "",
        "Each figure compares:",
        "",
        "- best dual-ports unroll result",
        "- best three-ports unroll result",
        "",
        "Both curves are aligned by the first non-zero compute IPC cycle.",
        "",
        "| Operator | I | Dual Best U | Three Best U | Figure |",
        "|---|---:|---:|---:|---|",
    ]

    for row in rows:
        operator = row["operator"]
        trip = int(row["I"])
        dual_u = int(row["dual_best_U"])
        three_u = int(row["three_best_U"])

        dual_log = (
            UNROLL_RUNS_ROOT
            / operator
            / f"I{trip}"
            / f"U{dual_u}"
            / "dual_ports"
            / "model"
            / "done_by_cycle.json"
        )
        three_log = (
            UNROLL_RUNS_ROOT
            / operator
            / f"I{trip}"
            / f"U{three_u}"
            / "three_ports"
            / "model"
            / "done_by_cycle.json"
        )
        out_name = f"{operator}_I{trip}_dualU{dual_u}_vs_threeU{three_u}.png"
        out_path = OUT_DIR / out_name
        title = f"{operator} I={trip} dual U={dual_u} vs three U={three_u}"

        cmd = [
            sys.executable,
            str(PLOT_SCRIPT),
            "--dual-log",
            str(dual_log),
            "--three-log",
            str(three_log),
            "--title",
            title,
            "--out",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, cwd=str(ROOT))
        rel_path = out_path.relative_to(ROOT)
        md_lines.append(
            f"| `{operator}` | {trip} | {dual_u} | {three_u} | [{out_name}](/d:/VfSimulator/{rel_path.as_posix()}) |"
        )

    index_path = OUT_DIR / "README.md"
    index_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Generated {len(rows)} IPC comparison figures in {OUT_DIR}")
    print(f"Index written to {index_path}")


if __name__ == "__main__":
    main()
