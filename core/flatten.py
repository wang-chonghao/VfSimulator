import argparse
import json
from typing import Any, Dict, List


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float))


def resolve_value(v, params, default=None):
    """
    Resolve v which can be:
      - int/float -> return as-is
      - string -> if in params, return params[string], else return string
      - None -> default
    """
    if v is None:
        return default
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v in params:
            return params[v]
        return v
    return v


def contains_any_loop(node):
    """
    Return True if node (list/dict) contains any loop node at any depth.
    Used to decide whether a loop is innermost.
    """
    if isinstance(node, list):
        for x in node:
            if contains_any_loop(x):
                return True
        return False

    if isinstance(node, dict):
        if node.get("type") == "loop":
            return True
        # For safety, scan common container fields
        for k in ("body", "program", "nodes", "then", "else"):
            if k in node:
                if contains_any_loop(node[k]):
                    return True
        return False

    return False


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(payload: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


class Flattener:

    def __init__(self, params):
        self.params = params
        self._next_loop_id = 0
        self._pc = 0
        self.linear = []

    def flatten(self, program):
        self.linear = []
        self._next_loop_id = 0
        self._pc = 0
        self._visit(program, depth=0, loop_stack=[])
        return self.linear

    def _visit(self, node, depth, loop_stack):
        if isinstance(node, list):
            for x in node:
                self._visit(x, depth=depth, loop_stack=loop_stack)
            return

        if not isinstance(node, dict):
            raise ValueError("Invalid node type")

        ntype = node.get("type")

        if ntype == "inst":
            self._emit_inst(node, depth, loop_stack)
            return

        if ntype == "membar":
            self._emit_membar(node, depth, loop_stack)
            return

        if ntype == "loop":
            self._emit_loop(node, depth, loop_stack)
            return

        raise ValueError("Unknown node type")

    def _emit_inst(self, inst, depth, loop_stack):
        out = dict(inst)
        out["type"] = "inst"
        out["pc"] = self._pc
        out["depth"] = depth
        out["loop_stack"] = list(loop_stack)
        out.setdefault("dst", [])
        out.setdefault("src", [])
        self.linear.append(out)
        self._pc += 1

    def _emit_membar(self, membar, depth, loop_stack):
        out = dict(membar)
        out["type"] = "membar"
        out["pc"] = self._pc
        out["depth"] = depth
        out["loop_stack"] = list(loop_stack)
        self.linear.append(out)
        self._pc += 1

    def _emit_loop(self, loop_node, depth, loop_stack):
        loop_id = self._next_loop_id
        self._next_loop_id += 1

        iters_raw = loop_node.get("iters")
        iters = resolve_value(iters_raw, self.params, default=1)

        # Unroll: default 1 if not provided
        unroll_raw = loop_node.get("unroll", 1)
        unroll = resolve_value(unroll_raw, self.params, default=1)

        # Normalize unroll
        if isinstance(unroll, float) and unroll.is_integer():
            unroll = int(unroll)
        if not isinstance(unroll, str):
            if not isinstance(unroll, int) or unroll < 1:
                raise ValueError(f"Invalid unroll={unroll} for loop {loop_node}. Must be int >= 1")

        name = loop_node.get("name", f"loop_{loop_id}")
        body = loop_node.get("body")
        if body is None:
            raise ValueError("Loop missing body")

        # Only innermost loop can unroll (>1)
        is_innermost = not contains_any_loop(body)
        if not is_innermost:
            if (isinstance(unroll, int) and unroll != 1) or (isinstance(unroll, str) and unroll_raw != 1):
                raise ValueError(
                    f"Only innermost loop can unroll. Loop '{name}' is not innermost but has unroll={unroll_raw}."
                )
            unroll = 1

        begin = {
            "type": "loop_begin",
            "op": "VLOOPv2",
            "pc": self._pc,
            "depth": depth + 1,
            "loop_id": loop_id,
            "iters": iters,
            "iters_raw": iters_raw,
            "unroll": unroll,
            "unroll_raw": unroll_raw,
            "name": name,
            "is_innermost": is_innermost,
            "loop_stack": list(loop_stack),
        }
        self.linear.append(begin)
        self._pc += 1

        new_stack = list(loop_stack) + [loop_id]
        self._visit(body, depth + 1, new_stack)

        end = {
            "type": "loop_end",
            "op": "VLOOPv2",
            "pc": self._pc,
            "depth": depth + 1,
            "loop_id": loop_id,
            "name": name,
            "loop_stack": list(loop_stack),
        }
        self.linear.append(end)
        self._pc += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_json")
    ap.add_argument("-o", "--out", default="linear.json")
    args = ap.parse_args()

    trace = load_json(args.trace_json)

    params = trace.get("params", {}) or {}
    program = trace.get("program")
    if program is None:
        raise ValueError("Missing key: program")

    flattener = Flattener(params)
    linear = flattener.flatten(program)

    save_json(
        {
            "dtype": trace.get("dtype", "fp32"),
            "params": params,
            "linear": linear,
        },
        args.out,
    )

    print("OK")


if __name__ == "__main__":
    main()
