from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from api.vf_costmodel import Membar, MemInfo, VFInfo, VFInst, VFLoop, VFNode


_FUNC_RE = re.compile(
    r"\bvoid\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>.*?)\)\s*\{",
    re.DOTALL,
)
_PRAGMA_UNROLL_RE = re.compile(r"#\s*pragma\s+unroll\s*\(\s*(\d+)\s*\)")
_VECTOR_DECL_RE = re.compile(r"\bvector_([A-Za-z0-9_]+)\s+([^;]+);")
_CALL_RE = re.compile(r"([A-Za-z_]\w*)\s*\((.*)\)\s*;", re.DOTALL)
_LOAD_OPS = {"VLD", "VLDS"}
_STORE_OPS = {"VST", "VSTS", "VSTUS", "VSTAS"}


@dataclass(frozen=True)
class CCEVFScope:
    kernel_name: str
    start_line: int
    end_line: int
    source: str
    params: Sequence[str]


def list_cce_vf_kernels(path: str | Path) -> List[str]:
    """Return all function names that contain a ``__VEC_SCOPE__`` block."""

    return [scope.kernel_name for scope in extract_cce_vf_scopes(path)]


def extract_cce_vf_scopes(path: str | Path) -> List[CCEVFScope]:
    """Extract every ``__VEC_SCOPE__`` block from a CCE/DSL source file."""

    source_path = Path(path)
    source = source_path.read_text(encoding="utf-8")
    clean = _strip_comments(source)

    scopes: List[CCEVFScope] = []
    for match in _FUNC_RE.finditer(clean):
        name = match.group("name")
        open_brace = match.end() - 1
        close_brace = _find_matching_brace(clean, open_brace)
        body = clean[open_brace + 1 : close_brace]

        vec_match = re.search(r"__VEC_SCOPE__\s*\{", body)
        if not vec_match:
            continue

        vec_open = open_brace + 1 + vec_match.end() - 1
        vec_close = _find_matching_brace(clean, vec_open)
        vec_source = clean[vec_open + 1 : vec_close]
        scopes.append(
            CCEVFScope(
                kernel_name=name,
                start_line=_line_number(clean, vec_open),
                end_line=_line_number(clean, vec_close),
                source=vec_source,
                params=_parse_param_names(match.group("params")),
            )
        )

    return scopes


def parse_cce_vf_info(
    path: str | Path,
    kernel_name: str | None = None,
    loop_params: Optional[Dict[str, int]] = None,
) -> VFInfo:
    """
    Parse one CCE ``__VEC_SCOPE__`` kernel into ``VFInfo``.

    ``loop_params`` can provide concrete values for symbolic loop bounds such as
    ``repeat_times``. If omitted, the adapter tries a small whole-file inference
    pass for calls like ``int repeat_times = 96; gelu_simd_ub(..., repeat_times)``.
    """

    source_path = Path(path)
    source = _strip_comments(source_path.read_text(encoding="utf-8"))
    scope = _select_scope(extract_cce_vf_scopes(source_path), kernel_name)
    resolved_loop_params = dict(loop_params or {})
    resolved_loop_params.update(_infer_call_argument_constants(source, scope))
    parser = _VFScopeParser(scope, resolved_loop_params)
    return VFInfo(context=parser.parse())


class _VFScopeParser:
    def __init__(self, scope: CCEVFScope, loop_params: Dict[str, int]) -> None:
        self.scope = scope
        self.loop_params = loop_params
        self.register_dtypes = _extract_vector_decls(scope.source)
        self.register_names = set(self.register_dtypes)
        self.ub_names = set(_ub_param_names(scope.params))

    def parse(self) -> List[VFNode]:
        return self._parse_block(self.scope.source)

    def _parse_block(self, text: str) -> List[VFNode]:
        nodes: List[VFNode] = []
        pos = 0
        pending_unroll = 1

        while pos < len(text):
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                break

            pragma = _PRAGMA_UNROLL_RE.match(text, pos)
            if pragma:
                pending_unroll = int(pragma.group(1))
                pos = pragma.end()
                continue

            if text.startswith("for", pos) and _is_token_boundary(text, pos + 3):
                header_start = text.find("(", pos)
                if header_start < 0:
                    raise ValueError("Malformed for-loop in __VEC_SCOPE__")
                header_end = _find_matching_paren(text, header_start)
                body_open = text.find("{", header_end)
                if body_open < 0:
                    raise ValueError("Only braced for-loops are supported in __VEC_SCOPE__")
                body_close = _find_matching_brace(text, body_open)
                count = self._loop_count_from_header(text[header_start + 1 : header_end])
                body = self._parse_block(text[body_open + 1 : body_close])
                nodes.append(VFLoop(count=count, unroll=pending_unroll, body=body))
                pending_unroll = 1
                pos = body_close + 1
                continue

            stmt_end = text.find(";", pos)
            if stmt_end < 0:
                trailing = text[pos:].strip()
                if trailing:
                    raise ValueError(f"Unsupported trailing CCE text: {trailing[:80]}")
                break

            stmt = text[pos : stmt_end + 1].strip()
            node = self._parse_statement(stmt)
            if node is not None:
                nodes.append(node)
            pos = stmt_end + 1

        return nodes

    def _parse_statement(self, stmt: str) -> VFNode | None:
        if not stmt:
            return None
        if stmt.startswith("vector_"):
            return None
        if "pset_" in stmt:
            return None

        match = _CALL_RE.fullmatch(stmt)
        if not match:
            return None

        callee = match.group(1)
        args = _split_args(match.group(2))
        low = callee.lower()
        if "barrier" in low or "membar" in low:
            return Membar()
        if not low.startswith("v"):
            return None

        op = _normalize_op(callee)
        if op in _LOAD_OPS:
            if len(args) < 2:
                raise ValueError(f"{callee} expects at least dst and UB source")
            dst = self._register_operand(args[0])
            return VFInst(
                name=op,
                form=dst.dtype,
                dst=[dst],
                src=[MemInfo(args[1], "UB")],
            )
        if op in _STORE_OPS:
            if len(args) < 2:
                raise ValueError(f"{callee} expects at least register source and UB dst")
            src = self._register_operand(args[0])
            return VFInst(
                name=op,
                form=src.dtype,
                src=[src],
                dst=[MemInfo(args[1], "UB")],
            )

        if not args:
            raise ValueError(f"{callee} expects at least one destination operand")
        dst = [self._register_operand(args[0])]
        src = [operand for arg in args[1:] if (operand := self._operand_for_arg(arg))]
        form = _infer_inst_form(op, dst, src)
        return VFInst(name=_specialize_op_for_form(op, form), form=form, src=src, dst=dst)

    def _register_operand(self, arg: str) -> MemInfo:
        name = _base_identifier(arg)
        return MemInfo(name, "Register", self.register_dtypes.get(name))

    def _operand_for_arg(self, arg: str) -> MemInfo | None:
        name = _base_identifier(arg)
        if name in self.register_names:
            return MemInfo(name, "Register", self.register_dtypes.get(name))
        if name in self.ub_names:
            return MemInfo(name, "UB")
        return None

    def _loop_count_from_header(self, header: str) -> int:
        parts = [part.strip() for part in header.split(";")]
        if len(parts) < 2:
            raise ValueError(f"Unsupported for-loop header: {header}")
        init = parts[0]
        cond = parts[1]
        step_expr = parts[2] if len(parts) >= 3 else ""

        init_match = re.search(r"([A-Za-z_]\w*)\s*=\s*(.+)$", init)
        cond_match = re.search(r"([A-Za-z_]\w*)\s*(<|<=)\s*(.+)$", cond)
        if not init_match or not cond_match:
            raise ValueError(f"Unsupported for-loop header: {header}")

        var = init_match.group(1)
        if cond_match.group(1) != var:
            raise ValueError(f"Unsupported for-loop variable mismatch: {header}")

        start = _resolve_count_expr(init_match.group(2), self.loop_params)
        bound = _resolve_count_expr(cond_match.group(3), self.loop_params)
        step = _resolve_loop_step(step_expr, var, self.loop_params)
        if step <= 0:
            raise ValueError(f"Only positive for-loop steps are supported: {header}")

        inclusive = cond_match.group(2) == "<="
        span = bound - start + (1 if inclusive else 0)
        if span <= 0:
            return 0
        return (span + step - 1) // step


def _select_scope(scopes: Sequence[CCEVFScope], kernel_name: str | None) -> CCEVFScope:
    if not scopes:
        raise ValueError("No __VEC_SCOPE__ kernel found in CCE file")
    if kernel_name is None:
        if len(scopes) == 1:
            return scopes[0]
        names = ", ".join(scope.kernel_name for scope in scopes)
        raise ValueError(f"Multiple __VEC_SCOPE__ kernels found; select one of: {names}")

    matches = [scope for scope in scopes if scope.kernel_name == kernel_name]
    if not matches:
        names = ", ".join(scope.kernel_name for scope in scopes)
        raise ValueError(f"Kernel '{kernel_name}' not found. Available kernels: {names}")
    return matches[0]


def _parse_param_names(params: str) -> List[str]:
    names: List[str] = []
    for raw in _split_args(params):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.search(r"([A-Za-z_]\w*)\s*(?:=[^,]+)?$", cleaned)
        if match:
            names.append(match.group(1))
    return names


def _ub_param_names(params: Sequence[str]) -> List[str]:
    return [param for param in params if param]


def _infer_call_argument_constants(source: str, scope: CCEVFScope) -> Dict[str, int]:
    constants: Dict[str, int] = {}
    assign_re = re.compile(r"\b(?:int|uint16_t|uint32_t|int32_t)\s+([A-Za-z_]\w*)\s*=\s*(\d+)\s*;")
    call_re = re.compile(rf"\b{re.escape(scope.kernel_name)}\s*\((.*?)\)\s*;", re.DOTALL)

    for call in call_re.finditer(source):
        args = _split_args(call.group(1))
        prefix = source[: call.start()]
        assigned = {name: int(value) for name, value in assign_re.findall(prefix)}
        for param, arg in zip(scope.params, args):
            arg_name = _base_identifier(arg)
            if arg_name in assigned:
                constants[param] = assigned[arg_name]
            elif arg_name.isdigit():
                constants[param] = int(arg_name)
    return constants


def _resolve_count_expr(expr: str, loop_params: Dict[str, int]) -> int:
    expr = expr.strip()
    while True:
        cast_match = re.fullmatch(r"[A-Za-z_]\w*\s*\(\s*(.+)\s*\)", expr)
        if not cast_match:
            break
        expr = cast_match.group(1).strip()
    if expr.isdigit():
        return int(expr)
    name = _base_identifier(expr)
    if name in loop_params:
        return int(loop_params[name])
    raise ValueError(
        f"Cannot resolve loop bound '{expr}'. Pass loop_params={{'{name}': ...}} "
        "or use a constant loop bound."
    )


def _resolve_loop_step(step_expr: str, var: str, loop_params: Dict[str, int]) -> int:
    expr = step_expr.strip()
    if not expr:
        return 1
    if re.fullmatch(rf"\+\+\s*{re.escape(var)}|{re.escape(var)}\s*\+\+", expr):
        return 1
    plus_eq = re.fullmatch(rf"{re.escape(var)}\s*\+=\s*(.+)", expr)
    if plus_eq:
        return _resolve_count_expr(plus_eq.group(1), loop_params)
    assign_plus = re.fullmatch(rf"{re.escape(var)}\s*=\s*{re.escape(var)}\s*\+\s*(.+)", expr)
    if assign_plus:
        return _resolve_count_expr(assign_plus.group(1), loop_params)
    raise ValueError(f"Unsupported for-loop step: {step_expr}")


def _normalize_op(callee: str) -> str:
    return callee.upper()


def _vector_dtype_to_form(dtype: str) -> str:
    text = dtype.lower()
    mapping = {
        "f32": "fp32",
        "float32": "fp32",
        "fp32": "fp32",
        "f16": "fp16",
        "float16": "fp16",
        "fp16": "fp16",
        "s32": "int32",
        "i32": "int32",
        "int32": "int32",
        "u32": "uint32",
        "uint32": "uint32",
    }
    return mapping.get(text, text)


def _extract_vector_decls(source: str) -> Dict[str, str]:
    register_dtypes: Dict[str, str] = {}
    for dtype, names_text in _VECTOR_DECL_RE.findall(source):
        form = _vector_dtype_to_form(dtype)
        for raw_name in names_text.split(","):
            name = _base_identifier(raw_name)
            if name:
                register_dtypes[name] = form
    return register_dtypes


def _infer_inst_form(op: str, dst: Sequence[MemInfo], src: Sequence[MemInfo]) -> str | None:
    op = op.upper()
    src_dtype = next((operand.dtype for operand in src if operand.dtype), None)
    dst_dtype = next((operand.dtype for operand in dst if operand.dtype), None)
    if op in {"VCVT_F32_TO_F16", "VCVT_F16_TO_F32", "VCVT_F32_TO_S32", "VCVT_S32_TO_F32"}:
        explicit = {
            "VCVT_F32_TO_F16": "f32_to_f16",
            "VCVT_F16_TO_F32": "f16_to_f32",
            "VCVT_F32_TO_S32": "f32_to_s32",
            "VCVT_S32_TO_F32": "s32_to_f32",
        }
        return explicit[op]
    if op == "VCVT" and src_dtype and dst_dtype:
        compact = {
            "fp32": "f32",
            "fp16": "f16",
            "int32": "s32",
        }
        src_key = compact.get(src_dtype)
        dst_key = compact.get(dst_dtype)
        if src_key and dst_key:
            return f"{src_key}_to_{dst_key}"
    return dst_dtype or src_dtype


def _specialize_op_for_form(op: str, form: str | None) -> str:
    op = op.upper()
    if op != "VCVT" or not form:
        return op
    mapping = {
        "f32_to_f16": "VCVT_F32_TO_F16",
        "f16_to_f32": "VCVT_F16_TO_F32",
        "f32_to_s32": "VCVT_F32_TO_S32",
        "s32_to_f32": "VCVT_S32_TO_F32",
    }
    return mapping.get(form, op)


def _split_args(text: str) -> List[str]:
    args: List[str] = []
    start = 0
    depth = 0
    for idx, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def _base_identifier(arg: str) -> str:
    match = re.search(r"[A-Za-z_]\w*", arg.strip())
    return match.group(0) if match else arg.strip()


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.DOTALL)
    source = re.sub(r"//.*", "", source)
    return source


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _is_token_boundary(text: str, pos: int) -> bool:
    return pos >= len(text) or not (text[pos].isalnum() or text[pos] == "_")


def _find_matching_brace(text: str, open_pos: int) -> int:
    return _find_matching_delim(text, open_pos, "{", "}")


def _find_matching_paren(text: str, open_pos: int) -> int:
    return _find_matching_delim(text, open_pos, "(", ")")


def _find_matching_delim(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    if open_pos < 0 or open_pos >= len(text) or text[open_pos] != open_ch:
        raise ValueError(f"Expected '{open_ch}' at position {open_pos}")
    depth = 0
    for idx in range(open_pos, len(text)):
        ch = text[idx]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    raise ValueError(f"Unmatched '{open_ch}' at position {open_pos}")


def _line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1
