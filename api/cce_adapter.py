from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from api.input_symbols import (
    compact_dtype,
    normalize_dtype,
    normalize_membar_type,
    normalize_opcode,
)
from api.frontend.instruction_catalog import (
    ArgumentKind,
    DEFAULT_INSTRUCTION_CATALOG,
    FormRule,
    InstructionSpec,
    OperandDirection,
)
from api.vf_info import (
    Membar,
    MemInfo,
    VFInfo,
    VFInst,
    VFLoop,
    VFNode,
    canonicalize_vf_info,
)


_FUNC_RE = re.compile(
    r"\bvoid\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>.*?)\)\s*\{",
    re.DOTALL,
)
_PRAGMA_UNROLL_RE = re.compile(r"#\s*pragma\s+unroll\s*\(\s*(\d+)\s*\)")
_VECTOR_DECL_STMT_RE = re.compile(r"^\s*vector_([A-Za-z0-9_]+)\s+([^;]+)\s*$")
_CALL_RE = re.compile(r"([A-Za-z_]\w*)\s*\((.*)\)\s*;", re.DOTALL)


@dataclass(frozen=True)
class CCEVFScope:
    kernel_name: str
    start_line: int
    end_line: int
    source: str
    declaration_source: str
    params: Sequence[str]
    param_storage: Dict[str, str]


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
                declaration_source=body,
                params=_parse_param_names(match.group("params")),
                param_storage=_parse_param_storage(match.group("params")),
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
    return canonicalize_vf_info(VFInfo(context=parser.parse()))


class _VFScopeParser:
    def __init__(self, scope: CCEVFScope, loop_params: Dict[str, int]) -> None:
        self.scope = scope
        self.loop_params = loop_params
        self.register_dtypes = _extract_vector_decls(scope.declaration_source)
        self.register_names = set(self.register_dtypes)
        self.ub_names = {
            name for name, storage in scope.param_storage.items() if storage == "UB"
        }
        self.scalar_names = {
            name for name, storage in scope.param_storage.items() if storage == "Scalar"
        }

    def parse(self) -> List[VFNode]:
        return self._parse_block(self.scope.source, frozenset())

    def _parse_block(
        self,
        text: str,
        induction_variables: frozenset[str],
    ) -> List[VFNode]:
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
                count, variable = self._loop_count_from_header(
                    text[header_start + 1 : header_end]
                )
                body = self._parse_block(
                    text[body_open + 1 : body_close],
                    induction_variables | {variable},
                )
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
            node = self._parse_statement(stmt, induction_variables)
            if node is not None:
                nodes.append(node)
            pos = stmt_end + 1

        return nodes

    def _parse_statement(
        self,
        stmt: str,
        induction_variables: frozenset[str],
    ) -> VFNode | None:
        if not stmt:
            return None
        if stmt.startswith("vector_"):
            self._record_vector_decl_statement(stmt)
            return None
        smem_bar = re.match(r"SMEM_BAR\s*\.\s*([A-Za-z_]\w*)\s*;", stmt, re.IGNORECASE)
        if smem_bar:
            return Membar(normalize_membar_type(smem_bar.group(1)))

        match = _CALL_RE.fullmatch(stmt)
        if not match:
            return None

        callee = match.group(1)
        args = _split_args(match.group(2))
        low = callee.lower()
        if low.startswith("pset_"):
            return None
        if low in {"mem_bar", "membar"} or "barrier" in low:
            barrier = args[0] if args else None
            return Membar(normalize_membar_type(barrier))
        if not low.startswith("v"):
            return None

        op = normalize_opcode(callee)
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
        if spec is None:
            return self._bind_generic_compute_call(callee, op, args)
        src, dst = self._bind_catalog_call(
            callee, spec, args, induction_variables
        )
        form = _infer_inst_form(op, dst, src)
        resolved_op, resolved_form = DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(
            op, form
        )
        return VFInst(
            name=resolved_op,
            form=resolved_form,
            src=src,
            dst=dst,
        )

    def _bind_catalog_call(
        self,
        callee: str,
        spec: InstructionSpec,
        args: Sequence[str],
        induction_variables: frozenset[str],
    ) -> tuple[List[MemInfo], List[MemInfo]]:
        src: List[MemInfo] = []
        dst: List[MemInfo] = []
        expected_count = max(
            (operand.argument_index for operand in spec.operands), default=-1
        ) + 1
        if len(args) > expected_count:
            raise ValueError(
                f"{callee} expects at most {expected_count} arguments, got {len(args)}"
            )
        for operand_spec in spec.operands:
            index = operand_spec.argument_index
            if index >= len(args):
                if operand_spec.optional:
                    continue
                raise ValueError(
                    f"{callee} is missing required argument {index} "
                    f"({operand_spec.name})"
                )
            operand = self._bind_catalog_argument(
                callee,
                args[index],
                operand_spec,
                index,
                induction_variables,
            )
            if operand is None or operand_spec.direction == OperandDirection.IGNORE:
                continue
            if operand_spec.direction == OperandDirection.INPUT:
                src.append(operand)
            else:
                dst.append(operand)
        return src, dst

    def _bind_catalog_argument(
        self,
        callee: str,
        arg: str,
        operand_spec,
        index: int,
        induction_variables: frozenset[str],
    ) -> MemInfo | None:
        kind = operand_spec.kind
        name = _base_identifier(arg)
        if kind == ArgumentKind.REGISTER:
            if name not in self.register_names:
                raise ValueError(
                    f"{callee} argument {index} must be a declared vector register: {arg}"
                )
            return MemInfo(name, "Register", self.register_dtypes.get(name))
        if kind == ArgumentKind.UB:
            if name not in self.ub_names:
                raise ValueError(
                    f"{callee} argument {index} must be a declared UB object: {arg}"
                )
            return MemInfo(name, "UB")
        if kind in {ArgumentKind.SCALAR, ArgumentKind.REGISTER_OR_SCALAR}:
            if name in self.register_names:
                if kind == ArgumentKind.SCALAR:
                    raise ValueError(
                        f"{callee} argument {index} must be scalar: {arg}"
                    )
                return MemInfo(name, "Register", self.register_dtypes.get(name))
            if name in self.ub_names:
                raise ValueError(
                    f"{callee} argument {index} cannot use a UB object as scalar: {arg}"
                )
            if name in self.scalar_names:
                return MemInfo(name, "Scalar")
            if _is_numeric_scalar_literal(arg):
                return None
            raise ValueError(
                f"{callee} argument {index} must be a declared scalar or literal: {arg}"
            )
        if kind == ArgumentKind.PREDICATE:
            text = arg.strip().lower()
            if (
                re.fullmatch(r"pset_[a-z0-9_]+\s*\(.*\)", text, re.DOTALL)
                or self.register_dtypes.get(name) == "bool"
            ):
                return None
            raise ValueError(
                f"{callee} argument {index} must be a predicate: {arg}"
            )
        if kind == ArgumentKind.CONFIG:
            if operand_spec.name == "offset":
                allowed_names = (
                    set(induction_variables)
                    | set(self.loop_params)
                    | self.scalar_names
                )
                if _is_affine_int_expression(arg, allowed_names):
                    return None
                raise ValueError(
                    f"{callee} argument {index} has invalid offset expression: {arg}"
                )
            if (
                operand_spec.allowed_values
                and arg.strip() not in operand_spec.allowed_values
            ):
                raise ValueError(
                    f"{callee} argument {index} has unsupported configuration: {arg}"
                )
            if _is_config_token(arg):
                return None
            raise ValueError(
                f"{callee} argument {index} must be a configuration token: {arg}"
            )
        return None

    def _bind_generic_compute_call(
        self,
        callee: str,
        op: str,
        args: Sequence[str],
    ) -> VFInst:
        if not args:
            raise ValueError(f"{callee} expects at least one destination operand")
        dst = [self._register_operand(args[0])]
        src = [
            operand
            for arg in args[1:]
            if (operand := self._operand_for_arg(arg))
        ]
        form = _infer_inst_form(op, dst, src)
        return VFInst(name=op, form=form, src=src, dst=dst)

    def _register_operand(self, arg: str) -> MemInfo:
        name = _base_identifier(arg)
        if name not in self.register_names:
            raise ValueError(f"Expected declared vector register: {arg}")
        return MemInfo(name, "Register", self.register_dtypes.get(name))

    def _ub_operand(self, arg: str) -> MemInfo:
        return MemInfo(_base_identifier(arg), "UB")

    def _operand_for_arg(self, arg: str) -> MemInfo | None:
        name = _base_identifier(arg)
        if name in self.register_names:
            return MemInfo(name, "Register", self.register_dtypes.get(name))
        if name in self.ub_names:
            return MemInfo(name, "UB")
        if name in self.scalar_names:
            return MemInfo(name, "Scalar")
        return None

    def _record_vector_decl_statement(self, stmt: str) -> None:
        clean = stmt.strip().rstrip(";").strip()
        match = _VECTOR_DECL_STMT_RE.fullmatch(clean)
        if not match:
            return
        dtype, names_text = match.groups()
        form = _vector_dtype_to_form(dtype)
        for raw_name in _split_args(names_text):
            name = _declared_identifier(raw_name)
            if name:
                self.register_dtypes[name] = form
                self.register_names.add(name)

    def _loop_count_from_header(self, header: str) -> tuple[int, str]:
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
            return 0, var
        return (span + step - 1) // step, var


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
    return list(_parse_param_storage(params))


def _parse_param_storage(params: str) -> Dict[str, str]:
    storage: Dict[str, str] = {}
    for raw in _split_args(params):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.search(r"([A-Za-z_]\w*)\s*(?:=[^,]+)?$", cleaned)
        if not match:
            continue
        name = match.group(1)
        storage[name] = "UB" if "__ubuf__" in cleaned else "Scalar"
    return storage


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
    resolved = _eval_int_expr(expr, loop_params)
    if resolved is not None:
        return resolved
    name = _base_identifier(expr)
    raise ValueError(
        f"Cannot resolve loop bound '{expr}'. Pass loop_params={{'{name}': ...}} "
        "or use a constant loop bound."
    )


def _eval_int_expr(expr: str, names: Dict[str, int]) -> int | None:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> int | None:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return None
            if isinstance(node.value, int):
                return int(node.value)
            return None
        if isinstance(node, ast.Name):
            return int(names[node.id]) if node.id in names else None
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if value is None:
                return None
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            return None
        if isinstance(node, ast.BinOp):
            lhs = visit(node.left)
            rhs = visit(node.right)
            if lhs is None or rhs is None:
                return None
            if isinstance(node.op, ast.Add):
                return lhs + rhs
            if isinstance(node.op, ast.Sub):
                return lhs - rhs
            if isinstance(node.op, ast.Mult):
                return lhs * rhs
            if isinstance(node.op, ast.Div):
                if rhs == 0 or lhs % rhs != 0:
                    return None
                return lhs // rhs
            if isinstance(node.op, ast.FloorDiv):
                if rhs == 0:
                    return None
                return lhs // rhs
        return None

    return visit(tree)


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


def _vector_dtype_to_form(dtype: str) -> str:
    return str(normalize_dtype(dtype, default=str(dtype).lower()))


def _is_numeric_scalar_literal(value: str) -> bool:
    text = value.strip()
    text = re.sub(r"^[()]|[()]$", "", text).strip()
    return bool(re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFlL]?",
        text,
    ))


def _is_config_token(value: str) -> bool:
    text = value.strip()
    return bool(
        re.fullmatch(r"[A-Z][A-Z0-9_]*", text)
        or re.fullmatch(r"[+-]?\d+", text)
    )


def _is_affine_int_expression(value: str, allowed_names: set[str]) -> bool:
    try:
        tree = ast.parse(value.strip(), mode="eval")
    except SyntaxError:
        return False

    def valid(node: ast.AST) -> bool:
        if isinstance(node, ast.Expression):
            return valid(node.body)
        if isinstance(node, ast.Constant):
            return isinstance(node.value, int) and not isinstance(node.value, bool)
        if isinstance(node, ast.Name):
            return node.id in allowed_names
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, (ast.UAdd, ast.USub)) and valid(node.operand)
        if isinstance(node, ast.BinOp):
            return isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)) and valid(
                node.left
            ) and valid(node.right)
        return False

    return valid(tree)


def _declared_identifier(value: str) -> str:
    match = re.match(r"\s*([A-Za-z_]\w*)", value)
    return match.group(1) if match else ""


def _extract_vector_decls(source: str) -> Dict[str, str]:
    register_dtypes: Dict[str, str] = {}
    for stmt in source.split(";"):
        match = _VECTOR_DECL_STMT_RE.fullmatch(stmt)
        if not match:
            continue
        dtype, names_text = match.groups()
        form = _vector_dtype_to_form(dtype)
        for raw_name in _split_args(names_text):
            name = _declared_identifier(raw_name)
            if name:
                register_dtypes[name] = form
    return register_dtypes


def _infer_inst_form(op: str, dst: Sequence[MemInfo], src: Sequence[MemInfo]) -> str | None:
    op = normalize_opcode(op)
    src_dtype = next((operand.dtype for operand in src if operand.dtype), None)
    dst_dtype = next((operand.dtype for operand in dst if operand.dtype), None)
    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    if spec and spec.form_rule == FormRule.FIXED:
        return spec.fixed_form
    if spec and spec.form_rule == FormRule.CONVERSION and src_dtype and dst_dtype:
        src_key = compact_dtype(src_dtype)
        dst_key = compact_dtype(dst_dtype)
        if src_key and dst_key:
            return f"{src_key}_to_{dst_key}"
    return dst_dtype or src_dtype


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
    text = arg.strip()
    identifiers = re.findall(r"[A-Za-z_]\w*", text)
    type_tokens = {
        "__ubuf__",
        "bool",
        "const",
        "float",
        "half",
        "int",
        "int16_t",
        "int32_t",
        "int64_t",
        "int8_t",
        "long",
        "short",
        "signed",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "uint8_t",
        "unsigned",
        "vector_bool",
        "vector_f16",
        "vector_f32",
        "vector_s16",
        "vector_s32",
        "vector_u16",
        "vector_u32",
        "volatile",
    }
    for name in reversed(identifiers):
        if name not in type_tokens:
            return name
    return identifiers[-1] if identifiers else text


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
