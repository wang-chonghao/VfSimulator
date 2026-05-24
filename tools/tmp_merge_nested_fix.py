#!/usr/bin/env python3
import csv
import json
from pathlib import Path


def load_rows(path: Path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return data
    return data.get("rows", [])


def main() -> None:
    root = Path("results/nested_unroll_test/I2_J48_u123468")
    main_p = root / "summary.json"
    fix_ps = [
        Path("results/nested_unroll_test/I2_J48_u123468_fixbin/summary.json"),
        Path("results/nested_unroll_test/I2_J48_u123468_fixmix/summary.json"),
    ]

    rows = load_rows(main_p)
    idx = {(r["case"], int(r["U"])): dict(r) for r in rows}
    for fp in fix_ps:
        for r in load_rows(fp):
            rr = dict(r)
            rr.setdefault("error_reason", "")
            idx[(rr["case"], int(rr["U"]))] = rr

    cases = sorted({k[0] for k in idx})
    us = [1, 2, 3, 4, 6, 8]
    out = []
    for c in cases:
        for u in us:
            if (c, u) in idx:
                out.append(idx[(c, u)])

    (root / "summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    fields = [
        "case",
        "U",
        "model_vf_end",
        "cce_vf_end",
        "abs_err",
        "rel_err",
        "precision_pass",
        "check_note",
        "error_reason",
        "case_dir",
    ]
    with (root / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in fields})

    by_case = {}
    for r in out:
        by_case.setdefault(r["case"], []).append(r)

    detail = []
    strict = 0
    tol5 = 0
    n = 0
    for c, rs in by_case.items():
        umap = {int(r["U"]): r for r in rs}
        if any(u not in umap for u in us):
            continue
        if any(umap[u].get("cce_vf_end") in (None, "") for u in us):
            continue
        n += 1
        model_best_u = min(us, key=lambda u: float(umap[u]["model_vf_end"]))
        cce_best_u = min(us, key=lambda u: float(umap[u]["cce_vf_end"]))
        strict_hit = model_best_u == cce_best_u
        cce_at_model_best = float(umap[model_best_u]["cce_vf_end"])
        cce_best = float(umap[cce_best_u]["cce_vf_end"])
        tol_hit = ((cce_at_model_best - cce_best) / max(1.0, cce_best)) <= 0.05
        strict += int(strict_hit)
        tol5 += int(tol_hit)
        detail.append(
            {
                "case": c,
                "model_best_u": model_best_u,
                "cce_best_u": cce_best_u,
                "strict_hit": strict_hit,
                "tol5_hit": tol_hit,
                "model_pick_cce_vf": int(cce_at_model_best),
                "cce_best_vf": int(cce_best),
            }
        )

    stats = {
        "cases_with_cce": n,
        "strict_hit_count": strict,
        "strict_hit_rate": (strict / n) if n else 0.0,
        "tol5_hit_count": tol5,
        "tol5_hit_rate": (tol5 / n) if n else 0.0,
        "detail": detail,
    }
    (root / "sweetspot_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# Nested Unroll Accuracy Report (I=2, J=48)",
        "",
        "## Summary",
        "",
        f"- Cases with CCE: {n}",
        f"- Strict sweetspot hit: {strict} / {n}",
        f"- Strict hit rate: {stats['strict_hit_rate'] * 100:.2f}%",
        f"- 5% tolerant sweetspot hit: {tol5} / {n}",
        f"- 5% tolerant hit rate: {stats['tol5_hit_rate'] * 100:.2f}%",
        "",
        "## Full Table",
        "",
        "| case | U | model_vf_end | cce_vf_end | abs_err | rel_err | precision_pass | check_note | error_reason |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for r in out:
        try:
            rel = f"{float(r.get('rel_err', 0.0)) * 100:.2f}%"
        except Exception:
            rel = ""
        lines.append(
            f"| {r.get('case')} | {r.get('U')} | {r.get('model_vf_end')} | {r.get('cce_vf_end')} | "
            f"{r.get('abs_err')} | {rel} | {r.get('precision_pass')} | {r.get('check_note', '')} | "
            f"{r.get('error_reason', '')} |"
        )

    lines += [
        "",
        "## Sweetspot Detail",
        "",
        "| case | model_best_u | cce_best_u | strict_hit | tol5_hit | model_pick_cce_vf | cce_best_vf |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for d in detail:
        lines.append(
            f"| {d['case']} | {d['model_best_u']} | {d['cce_best_u']} | {d['strict_hit']} | "
            f"{d['tol5_hit']} | {d['model_pick_cce_vf']} | {d['cce_best_vf']} |"
        )
    (root / "accuracy_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"updated rows={len(out)} cases={len(cases)} cases_with_cce={n}")


if __name__ == "__main__":
    main()

