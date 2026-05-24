#!/usr/bin/env python3
import argparse
import csv
import json
import math
import re
import shutil
import subprocess
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISCHED0 = False
OOO_MODEL = "queue_level4"
ENABLE_VREG_LIVE_RANGE_NORMALIZATION = False
EXTRA_MODEL_ARGS = []

CASES = [
    {"id": "GeLU_poly", "json": "unroll_test/json/GeLU_poly.json", "dsl": "unroll_test/dsl/GeLU_poly.dsl", "verify": "gelu_poly", "inputs": 2, "outputs": 1},
    {"id": "GeLU", "json": "unroll_test/json/GeLU.json", "dsl": "unroll_test/dsl/GeLU.dsl", "verify": "gelu", "inputs": 2, "outputs": 1},
    {"id": "online_update", "json": "unroll_test/json/online_update.json", "dsl": "unroll_test/dsl/online_update.dsl", "verify": "online_update", "inputs": 4, "outputs": 4},
    {"id": "SiLU", "json": "unroll_test/json/SiLU.json", "dsl": "unroll_test/dsl/SiLU.dsl", "verify": "silu", "inputs": 2, "outputs": 1},
    {"id": "SwiGLU", "json": "unroll_test/json/SwiGLU.json", "dsl": "unroll_test/dsl/SwiGLU.dsl", "verify": "swiglu", "inputs": 2, "outputs": 1},
    {"id": "VADDS_chain64", "json": "unroll_test/json/VADDS_chain64.json", "dsl": "unroll_test/dsl/VADDS_chain64.dsl", "verify": "vadds64", "inputs": 2, "outputs": 1},
    {"id": "VEXP_chain8", "json": "unroll_test/json/VEXP_chain8.json", "dsl": "unroll_test/dsl/VEXP_chain8.dsl", "verify": "vexp8", "inputs": 2, "outputs": 1},
    {"id": "mixed_long_short", "json": "unroll_test/json/mixed_long_short.json", "dsl": "unroll_test/dsl/mixed_long_short.dsl", "verify": "mixed", "inputs": 2, "outputs": 1},
    {"id": "binary_ops_dominant", "json": "unroll_test/json/binary_ops_dominant.json", "dsl": "unroll_test/dsl/binary_ops_dominant.dsl", "verify": "binary", "inputs": 2, "outputs": 1},
]

ABS_TOL = 1e-3
REL_TOL = 1e-3


def to_wsl_path(path: Path) -> str:
    s = str(path.resolve()).replace('\\', '/')
    if len(s) >= 2 and s[1] == ':':
        drive = s[0].lower()
        rest = s[2:]
        return f"/mnt/{drive}{rest}"
    return s



def run(cmd, cwd=None, allow_fail=False):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, encoding='utf-8', errors='ignore')
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0 and not allow_fail:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{out}")
    return out


def read_floats(path: Path):
    a = array('f')
    with path.open('rb') as f:
        a.frombytes(f.read())
    return a


def cmp_arrays(golden, out):
    mismatch = 0
    max_abs = 0.0
    max_rel = 0.0
    n = min(len(golden), len(out))
    for i in range(n):
        g = float(golden[i])
        o = float(out[i])
        if (math.isinf(g) and math.isinf(o) and ((g > 0) == (o > 0))) or (math.isnan(g) and math.isnan(o)):
            ae = 0.0
            re = 0.0
        else:
            ae = abs(o - g)
            re = ae / max(abs(g), 1.0)
        max_abs = max(max_abs, ae)
        max_rel = max(max_rel, re)
        if ae > ABS_TOL and re > REL_TOL:
            mismatch += 1
    return mismatch, max_abs, max_rel




def safe_div(a: float, b: float) -> float:
    if b == 0.0:
        if a == 0.0:
            return float('nan')
        return float('inf') if a > 0 else float('-inf')
    return a / b


def golden(kind, inp0, inp1, inp2=None, inp3=None):
    if kind == "gelu_poly":
        out = array('f')
        for x in inp0:
            x = float(x)
            xh = 0.5 * x
            t = 0.7071 * x
            t = min(t, 3.92)
            t = max(t, -3.92)
            t2 = t * t
            num = 0.5344 * t2 + 7.5517
            num = num * t2 + 101.62809
            num = num * t2 + 1393.8015
            num = num * t2 + 5063.7915
            num = num * t2 + 29639.3848
            num = num * t
            den = t2 + 31.2128582
            den = den * t2 + 308.569641
            den = den * t2 + 3023.12476
            den = den * t2 + 14243.3662
            den = den * t2 + 26267.2246
            y = (safe_div(num, den) + 1.0) * xh
            out.append(float(y))
        return out
    if kind == "gelu":
        out = array('f')
        for x in inp0:
            x = float(x)
            v2 = abs(x)
            v3 = v2 * -1.702
            v4 = x * 0.851
            v5 = math.exp(v4)
            v6 = x * v5
            v7 = x - v2
            v8 = v7 * v6
            v9 = math.exp(v3)
            v9 = v9 + 1.0
            v10 = safe_div(v8, v9)
            out.append(float(v10))
        return out
    if kind == "online_update":
        out = array('f')
        if inp2 is None or inp3 is None:
            raise ValueError("online_update golden requires input2/input3")
        for a, b, c, d in zip(inp0, inp1, inp2, inp3):
            a = float(a); b = float(b)
            c = float(c); d = float(d)
            v3 = max(a, b)
            v5 = math.exp(a - v3)
            v7 = math.exp(b - v3)
            v9 = v5 * c
            v11 = d * v7
            v12 = v9 + v11
            out.append(float(v12))
        return out
    if kind == "silu":
        out = array('f')
        for x in inp0:
            x = float(x)
            y = x / (1.0 + math.exp(-x))
            out.append(float(y))
        return out
    if kind == "swiglu":
        out = array('f')
        for a, b in zip(inp0, inp1):
            a = float(a); b = float(b)
            silu = a / (1.0 + math.exp(-a))
            out.append(float(silu * b))
        return out
    if kind == "vadds64":
        out = array('f')
        delta = 64 * 0.1
        for x in inp0:
            out.append(float(x + delta))
        return out
    if kind == "vexp8":
        out = array('f')
        for x in inp0:
            y = float(x)
            for _ in range(8):
                try:
                    y = math.exp(y)
                except OverflowError:
                    y = float('inf')
            out.append(float(y))
        return out
    if kind == "mixed":
        out = array('f')
        for a, b in zip(inp0, inp1):
            a = float(a); b = float(b)
            v2 = a * b
            v3 = math.exp(v2)
            v4 = v2 + 1.0
            v5 = safe_div(v4, v3)
            v6 = v5 + a
            v7 = v6 * 0.5
            out.append(float(v7))
        return out
    if kind == "binary":
        out = array('f')
        for a, b in zip(inp0, inp1):
            a = float(a); b = float(b)
            v2 = a * b
            v3 = v2 + a
            v4 = v3 - b
            v5 = max(v4, v2)
            v6 = min(v5, v3)
            v7 = safe_div(v6, v5)
            out.append(float(v7))
        return out
    raise ValueError(kind)


def patch_json(src: Path, dst: Path, I: int, U: int):
    obj = json.loads(src.read_text(encoding='utf-8-sig'))
    obj.setdefault('params', {})
    obj['params']['I'] = I
    obj['params']['U'] = U
    dst.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def patch_dsl(src: Path, dst: Path, I: int, U: int):
    txt = src.read_text(encoding='utf-8-sig')
    size_bytes = I * 64 * 4
    txt = re.sub(r'int\s+repeat_times\s*=\s*\d+\s*;', f'int repeat_times = {I};', txt)
    txt = re.sub(r'(?m)^\s*//\s*#pragma\s+unroll\s*\([^\)]*\)\s*\n?', '', txt)
    txt = re.sub(r'(?m)^\s*#pragma\s+unroll\s*\([^\)]*\)\s*\n?', '', txt)
    loop_pat = re.compile(
        r'for\s*\(\s*uint16_t\s+i\s*=\s*0\s*;\s*i\s*<\s*uint16_t\s*\(\s*repeat_times\s*\)\s*;\s*\+\+i\s*\)\s*\{',
        re.M,
    )
    txt, n_loop = loop_pat.subn(
        f'#pragma unroll({U})\n        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{',
        txt,
        count=1,
    )
    if n_loop == 0:
        raise RuntimeError(f'cannot inject pragma unroll into {src}')
    if 'ub_data_h_addr_0' in txt and 'ub_data_y_addr_0' in txt:
        txt = txt.replace('ub_data_y_addr_0', 'ub_data_h_addr_0')
    if 'ub_data_7_addr_0' in txt and 'ub_data_y_addr_0' in txt:
        txt = txt.replace('ub_data_y_addr_0', 'ub_data_7_addr_0')
    txt = re.sub(
        r'0\s*,\s*1\s*,\s*16\s*\*\s*1024\s*,\s*0\s*,\s*0\s*,\s*0\s*\)',
        '0, 1, repeat_times * 64 * 4, 0, 0, 0)',
        txt
    )
    # online_update has 8 UB slots; for larger I we must avoid slot overlap.
    if "ub_data_0_addr_0" in txt and "ub_data_7_addr_0" in txt:
        for i in range(8):
            imm = hex(i * size_bytes)
            txt = re.sub(
                rf'(__ubuf__\s+float\s+\*ub_data_{i}_addr_0\s*=\s*\(__ubuf__\s+float\*\)get_imm\()\s*0x[0-9a-fA-F]+\s*(\);)',
                rf'\g<1>{imm}\2',
                txt
            )
    dst.write_text(txt, encoding='utf-8')


def kernel_name_from_dsl(path: Path):
    txt = path.read_text(encoding='utf-8-sig')
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f'cannot parse kernel name from {path}')
    return m.group(1)


def parse_vf_cycles_from_vf_markers(popped_log: Path, instr_log: Path):
    ps = popped_log.read_text(encoding='utf-8', errors='ignore')
    ins = instr_log.read_text(encoding='utf-8', errors='ignore')
    pms = re.findall(r'\[(\d{8})\].*?\bVF\b', ps, flags=re.IGNORECASE)
    ims = re.findall(r'\[(\d{8})\].*?\bVF\b', ins, flags=re.IGNORECASE)
    if not pms or not ims:
        raise RuntimeError(f'no VF markers in {popped_log} or {instr_log}')
    return int(pms[0]), int(ims[-1])


def parse_cce_vf_end(popped_log: Path, instr_log: Path):
    try:
        vf_start, vf_end = parse_vf_cycles_from_vf_markers(popped_log, instr_log)
        return vf_end - vf_start
    except Exception:
        s = instr_log.read_text(encoding='utf-8', errors='ignore')
        ms = re.findall(r'vf_execute_time:\s*(\d+)', s)
        if not ms:
            raise RuntimeError(f'no usable VF timing in {popped_log} or {instr_log}')
        return int(ms[-1])


def run_one(case, I, U, out_root: Path, run_cce=True):
    cid = case['id']
    tag = f"{cid}_I{I}_U{U}"
    case_dir = out_root / tag
    case_dir.mkdir(parents=True, exist_ok=True)

    tmp_json = case_dir / 'trace_input.json'
    patch_json(ROOT / case['json'], tmp_json, I, U)

    model_out_dir = case_dir / 'model'
    model_cmd = ['python', str(ROOT / 'main.py'), '--trace', str(tmp_json), '--out_dir', str(model_out_dir), '--ooo-model', OOO_MODEL]
    if ENABLE_VREG_LIVE_RANGE_NORMALIZATION:
        model_cmd.append('--enable-vreg-live-range-normalization')
    if EXTRA_MODEL_ARGS:
        model_cmd.extend(EXTRA_MODEL_ARGS)
    out = run(model_cmd)
    m = re.search(r'VF end cycle \(with drain\)\s*=\s*(\d+)', out)
    if not m:
        raise RuntimeError(f'cannot parse model vf_end for {tag}')
    model_vf_end = int(m.group(1))

    cce_vf = None
    precision_pass = None
    mismatch = None
    max_abs = None
    max_rel = None

    if run_cce:
        gen_dsl = case_dir / f'{tag}.dsl'
        patch_dsl(ROOT / case['dsl'], gen_dsl, I, U)
        kname = kernel_name_from_dsl(gen_dsl)
        stem = tag
        total = I * 64

        build_cmd = ['wsl','-d','Ubuntu','--','bash','-lc', f"cd /mnt/d/VfSimulator && {'CCEC_EXTRA_FLAGS=\"-mllvm -cce-aicore-vec-misched=0\" ' if MISCHED0 else ''}bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(gen_dsl)} {stem}"]
        run(build_cmd)
        run_cmd = ['wsl','-d','Ubuntu','--','bash','-lc', f"cd /mnt/d/VfSimulator && bash ascend_runner/current/run_native_simexec.sh /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o {kname} {case['inputs']} {case['outputs']} {total}"]
        run(run_cmd, allow_fail=True)

        local_dump = case_dir / 'cce_dump'
        local_dump.mkdir(exist_ok=True)
        src_dir = f"/home/lenovo/msprof_run/{stem}_native_simexec"
        dst_dir = to_wsl_path(local_dump)
        needed = ["core0.veccore0.instr_log.dump", "core0.veccore0.instr_popped_log.dump", "input0.bin", "input1.bin", "input2.bin", "input3.bin", "output0.bin"]
        for fn in needed:
            copy_one = ['wsl','-d','Ubuntu','--','bash','-lc', f"if [ -f {src_dir}/{fn} ]; then cp -f {src_dir}/{fn} {dst_dir}/{fn}; fi"]
            run(copy_one, allow_fail=True)

        instr_log = local_dump / 'core0.veccore0.instr_log.dump'
        popped_log = local_dump / 'core0.veccore0.instr_popped_log.dump'
        cce_vf = parse_cce_vf_end(popped_log, instr_log)

        inp0 = read_floats(local_dump / 'input0.bin')
        inp1 = read_floats(local_dump / 'input1.bin') if (local_dump / 'input1.bin').exists() else array('f', [0.0] * len(inp0))
        inp2 = read_floats(local_dump / 'input2.bin') if (local_dump / 'input2.bin').exists() else None
        inp3 = read_floats(local_dump / 'input3.bin') if (local_dump / 'input3.bin').exists() else None
        out0 = read_floats(local_dump / 'output0.bin')
        g = golden(case['verify'], inp0, inp1, inp2, inp3)
        mismatch, max_abs, max_rel = cmp_arrays(g, out0)
        precision_pass = (mismatch == 0)

    return {
        'case': cid,
        'I': I,
        'U': U,
        'model_vf_end': model_vf_end,
        'cce_vf_end': cce_vf,
        'delta': (None if cce_vf is None else model_vf_end - cce_vf),
        'rel_err': (None if cce_vf is None else (model_vf_end - cce_vf) / cce_vf),
        'precision_pass': precision_pass,
        'mismatch': mismatch,
        'max_abs_err': max_abs,
        'max_rel_err': max_rel,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iters', nargs='+', type=int, default=[16,64,96])
    ap.add_argument('--unrolls', nargs='+', type=int, default=[1])
    ap.add_argument('--out-dir', default='results/unroll_test/sweep')
    ap.add_argument('--skip-cce', action='store_true')
    ap.add_argument('--misched0', action='store_true', help='compile CCE with -mllvm -cce-aicore-vec-misched=0')
    ap.add_argument(
        '--ooo-model',
        default='queue_level4',
        choices=['classical-cpu-type', 'consumer-done', 'queue_level1', 'queue_level2', 'queue_level3', 'queue_level4'],
    )
    ap.add_argument(
        '--enable-vreg-live-range-normalization',
        action='store_true',
        help='Enable pre-flatten vreg live-range normalization before cost-model simulation',
    )
    ap.add_argument(
        '--extra-model-args',
        nargs=argparse.REMAINDER,
        default=[],
        help='Extra arguments forwarded verbatim to main.py after the standard model flags',
    )
    args = ap.parse_args()

    global MISCHED0
    MISCHED0 = bool(args.misched0)
    global OOO_MODEL
    OOO_MODEL = str(args.ooo_model)
    global ENABLE_VREG_LIVE_RANGE_NORMALIZATION
    ENABLE_VREG_LIVE_RANGE_NORMALIZATION = bool(args.enable_vreg_live_range_normalization)
    global EXTRA_MODEL_ARGS
    EXTRA_MODEL_ARGS = list(args.extra_model_args or [])

    out_root = ROOT / args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []
    existing = out_root / 'summary.json'
    fallback_existing = out_root / 'latest_rows.json'
    seen = set()
    load_path = existing if existing.exists() else (fallback_existing if fallback_existing.exists() else None)
    if load_path is not None:
        try:
            rows = json.loads(load_path.read_text(encoding='utf-8'))
            for r in rows:
                seen.add((r.get('case'), int(r.get('I')), int(r.get('U'))))
        except Exception:
            rows = []
            seen = set()

    for U in args.unrolls:
        for I in args.iters:
            for c in CASES:
                key = (c['id'], I, U)
                if key in seen:
                    print(f"[skip] {c['id']} I={I} U={U}", flush=True)
                    continue
                print(f"[run] {c['id']} I={I} U={U}", flush=True)
                row = run_one(c, I, U, out_root, run_cce=not args.skip_cce)
                rows.append(row)
                seen.add(key)
                (out_root / 'latest_rows.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
                (out_root / 'summary.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')

    csv_path = out_root / 'summary.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['case','I','U','model_vf_end','cce_vf_end','delta','rel_err','precision_pass','mismatch','max_abs_err','max_rel_err'])
        w.writeheader()
        w.writerows(rows)

    (out_root / 'summary.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"[done] {csv_path}")


if __name__ == '__main__':
    main()
