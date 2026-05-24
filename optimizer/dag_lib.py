"""
DAG Abstraction Layer for VF Fusion Optimizer.

Converts a flat list of instructions (from a JSON trace) into a Directed Acyclic Graph (DAG)
where nodes are individual instruction instances and edges represent data dependencies (RAW).

Key design principle:
  The JSON trace reuses virtual register names (e.g., V0, V1 ping-pong).
  To build a true DAG, we track the *last writer* of each virtual register.
  Each instruction instance gets a unique node ID, and an edge is created from
  the producer node to the consumer node whenever a consumer reads a register
  that was previously written by a producer.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict


@dataclass
class DagNode:
    """A single instruction instance in the DAG."""
    id: int                         # unique node id (sequential)
    op: str                         # operation type: VLD, VADDS, VMUL, VST, ...
    dst: List[str]                  # destination registers/memory, e.g. ["V1"]
    src: List[str]                  # source registers/memory, e.g. ["V0"]
    predecessors: List[int] = field(default_factory=list)  # node ids this node depends on
    successors: List[int] = field(default_factory=list)    # node ids that depend on this node

    def is_load(self) -> bool:
        return self.op == "VLD"

    def is_store(self) -> bool:
        return self.op == "VST"

    def is_compute(self) -> bool:
        return not self.is_load() and not self.is_store()

    def __repr__(self):
        return f"Node({self.id}: {self.op} dst={self.dst} src={self.src})"


class OperatorDAG:
    """
    Directed Acyclic Graph representing an operator's instruction-level data flow.
    
    Build from a flat instruction list (the body of a single loop, without VLD/VST wrappers).
    The DAG captures all RAW dependencies by tracking virtual register producers.
    """

    def __init__(self):
        self.nodes: Dict[int, DagNode] = {}       # id -> DagNode
        self.num_nodes: int = 0
        self.entry_nodes: List[int] = []           # nodes with no predecessors (DAG roots)
        self.exit_nodes: List[int] = []            # nodes with no successors (DAG leaves)
    
    @classmethod
    def from_inst_list(cls, instructions: List[Dict[str, Any]]) -> "OperatorDAG":
        """
        Build DAG from a flat list of instruction dicts.
        
        Each instruction dict has:
            {"op": "VADDS", "dst": ["V1"], "src": ["V0"]}
        
        Virtual register names (starting with 'V') are tracked for dependencies.
        Memory names (starting with 'mem') are also tracked.
        """
        dag = cls()
        # Track the last writer of each virtual register / memory location
        # key: register name (e.g. "V0"), value: node id of the last writer
        last_writer: Dict[str, int] = {}

        for idx, inst in enumerate(instructions):
            op = inst.get("op", "UNKNOWN")
            dst = inst.get("dst", [])
            src = inst.get("src", [])
            if isinstance(dst, str): dst = [dst]
            if isinstance(src, str): src = [src]

            node = DagNode(
                id=idx,
                op=op,
                dst=list(dst),
                src=list(src),
            )
            dag.nodes[idx] = node
            dag.num_nodes += 1

            # Determine predecessors: for each source, find who last wrote it
            pred_set: Set[int] = set()
            for s in src:
                if s in last_writer:
                    pred_id = last_writer[s]
                    if pred_id not in pred_set:
                        pred_set.add(pred_id)
                        node.predecessors.append(pred_id)
                        dag.nodes[pred_id].successors.append(idx)

            # Update last_writer for each destination
            for d in dst:
                last_writer[d] = idx

        # Identify entry and exit nodes
        for nid, node in dag.nodes.items():
            if not node.predecessors:
                dag.entry_nodes.append(nid)
            if not node.successors:
                dag.exit_nodes.append(nid)

        return dag

    @classmethod
    def from_json_trace(cls, trace_path: str) -> Tuple["OperatorDAG", Dict[str, Any]]:
        """
        Build DAG from a full JSON trace file.
        
        The trace contains a "program" field with loop structures.
        We extract all instructions from ALL loop bodies (flattening nested loops)
        and build a single unified DAG.
        
        Returns: (dag, metadata) where metadata contains dtype, params, etc.
        """
        with open(trace_path, "r", encoding="utf-8") as f:
            trace = json.load(f)

        instructions = []
        program = trace.get("program", [])
        cls._collect_instructions(program, instructions)

        dag = cls.from_inst_list(instructions)

        metadata = {
            "dtype": trace.get("dtype", "fp32"),
            "params": trace.get("params", {}),
            "source_file": trace_path,
        }
        return dag, metadata

    @staticmethod
    def _collect_instructions(nodes: Any, out: List[Dict[str, Any]]) -> None:
        """Recursively collect all instruction nodes from a nested program structure."""
        if isinstance(nodes, list):
            for n in nodes:
                OperatorDAG._collect_instructions(n, out)
        elif isinstance(nodes, dict):
            if nodes.get("type") == "inst":
                out.append(nodes)
            elif nodes.get("type") == "loop":
                body = nodes.get("body", [])
                OperatorDAG._collect_instructions(body, out)

    # ---- Analysis methods ----

    def critical_path_length(self) -> int:
        """
        Compute the length of the longest path in the DAG (critical path).
        Uses topological order dynamic programming.
        """
        # Topological sort via Kahn's algorithm
        in_degree = {nid: len(self.nodes[nid].predecessors) for nid in self.nodes}
        queue = [nid for nid, d in in_degree.items() if d == 0]
        topo_order = []

        while queue:
            current = queue.pop(0)
            topo_order.append(current)
            for succ in self.nodes[current].successors:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        # DP: longest path ending at each node
        dist = {nid: 0 for nid in self.nodes}
        for nid in topo_order:
            for succ in self.nodes[nid].successors:
                if dist[succ] < dist[nid] + 1:
                    dist[succ] = dist[nid] + 1

        return max(dist.values()) if dist else 0

    def topological_sort(self) -> List[int]:
        """Return node IDs in topological order."""
        in_degree = {nid: len(self.nodes[nid].predecessors) for nid in self.nodes}
        queue = [nid for nid, d in in_degree.items() if d == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)
            for succ in self.nodes[current].successors:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        return result

    def width_at_depth(self) -> Dict[int, int]:
        """
        Compute the 'width' of the DAG at each depth level.
        Depth of a node = length of the longest path from any entry node to this node.
        Width at depth d = number of nodes at depth d.
        
        This is useful for understanding parallelism opportunities.
        """
        # Compute depth of each node
        topo = self.topological_sort()
        depth = {nid: 0 for nid in self.nodes}
        for nid in topo:
            for succ in self.nodes[nid].successors:
                if depth[succ] < depth[nid] + 1:
                    depth[succ] = depth[nid] + 1

        width: Dict[int, int] = defaultdict(int)
        for nid, d in depth.items():
            width[d] += 1
        return dict(width)

    # ---- Visualization ----

    def summary(self) -> str:
        """Return a human-readable summary of the DAG."""
        lines = []
        lines.append(f"=== Operator DAG Summary ===")
        lines.append(f"  Total nodes:          {self.num_nodes}")
        lines.append(f"  Entry nodes (roots):  {len(self.entry_nodes)} -> {self.entry_nodes[:10]}{'...' if len(self.entry_nodes) > 10 else ''}")
        lines.append(f"  Exit nodes (leaves):  {len(self.exit_nodes)} -> {self.exit_nodes[:10]}{'...' if len(self.exit_nodes) > 10 else ''}")
        lines.append(f"  Critical path length: {self.critical_path_length()}")

        # Op type distribution
        op_counts: Dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            op_counts[node.op] += 1
        lines.append(f"  Op distribution:      {dict(op_counts)}")

        # Width info
        widths = self.width_at_depth()
        max_width = max(widths.values()) if widths else 0
        lines.append(f"  Max parallelism:      {max_width} (at depth {[d for d,w in widths.items() if w == max_width]})")
        lines.append(f"  Total depth levels:   {len(widths)}")

        return "\n".join(lines)

    def print_nodes(self, max_nodes: int = 20) -> str:
        """Print the first N nodes with their edges."""
        lines = []
        lines.append(f"\n--- First {min(max_nodes, self.num_nodes)} nodes ---")
        for i, (nid, node) in enumerate(sorted(self.nodes.items())):
            if i >= max_nodes:
                lines.append(f"  ... ({self.num_nodes - max_nodes} more nodes)")
                break
            pred_str = f" <- [{', '.join(str(p) for p in node.predecessors)}]" if node.predecessors else " (ROOT)"
            succ_str = f" -> [{', '.join(str(s) for s in node.successors)}]" if node.successors else " (LEAF)"
            lines.append(f"  [{nid:4d}] {node.op:8s} dst={node.dst} src={node.src}{pred_str}{succ_str}")
        return "\n".join(lines)


# ---- CLI entry point for testing ----
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build and analyze DAG from a VF trace JSON")
    parser.add_argument("trace", help="Path to the JSON trace file")
    parser.add_argument("--nodes", type=int, default=20, help="Number of nodes to print")
    args = parser.parse_args()

    dag, meta = OperatorDAG.from_json_trace(args.trace)

    print(f"Source: {meta['source_file']}")
    print(f"dtype:  {meta['dtype']}")
    print(f"params: {meta['params']}")
    print()
    print(dag.summary())
    print(dag.print_nodes(args.nodes))


if __name__ == "__main__":
    main()
