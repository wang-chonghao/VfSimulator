"""
DAG Partitioner for VF Fusion Optimizer.

Takes an OperatorDAG and splits it into multiple partitions (Loops),
then generates simulator-ready JSON with auto-inserted VST/VLD at boundaries.

Design principles:
  1. The DAG represents pure computational dependencies (no VST/VLD noise).
  2. The partitioner decides WHERE to cut, based on a target chain length.
  3. When generating JSON, VST/VLD are auto-inserted at partition boundaries
     for any register that crosses from one loop to another.
  4. The initial partition uses the empirically discovered "sweet spot"
     (e.g., chain_length=8 for VADDS) as a warm start.
"""

from __future__ import annotations
import json
import os
import copy
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

# Import from sibling module
import sys
sys.path.insert(0, os.path.dirname(__file__))
from dag_lib import OperatorDAG, DagNode


class Partition:
    """
    Represents one partition (= one Loop) of the DAG.
    Contains a set of node IDs and metadata.
    """
    def __init__(self, partition_id: int, node_ids: List[int] = None):
        self.id = partition_id
        self.node_ids: List[int] = node_ids or []  # kept in topological order

    def __repr__(self):
        return f"Partition({self.id}, {len(self.node_ids)} nodes)"


class PartitionPlan:
    """
    A complete partitioning of a DAG into ordered partitions.
    """
    def __init__(self, dag: OperatorDAG, partitions: List[Partition]):
        self.dag = dag
        self.partitions = partitions  # ordered list

    @property
    def num_partitions(self):
        return len(self.partitions)

    def summary(self) -> str:
        lines = [f"=== Partition Plan: {self.num_partitions} partitions ==="]
        for p in self.partitions:
            ops = defaultdict(int)
            for nid in p.node_ids:
                ops[self.dag.nodes[nid].op] += 1
            lines.append(f"  Partition {p.id}: {len(p.node_ids)} nodes, ops={dict(ops)}")

        # Count cross-boundary edges (= number of VST/VLD pairs needed)
        node_to_part = {}
        for p in self.partitions:
            for nid in p.node_ids:
                node_to_part[nid] = p.id

        cross_edges = 0
        cross_regs = set()
        for p in self.partitions:
            for nid in p.node_ids:
                node = self.dag.nodes[nid]
                for pred_id in node.predecessors:
                    if node_to_part.get(pred_id, -1) != p.id:
                        cross_edges += 1
                        # Find the register that crosses
                        pred_node = self.dag.nodes[pred_id]
                        for d in pred_node.dst:
                            if d in node.src:
                                cross_regs.add((node_to_part[pred_id], p.id, d))

        lines.append(f"  Cross-boundary edges: {cross_edges}")
        lines.append(f"  VST/VLD pairs needed: {len(cross_regs)}")
        return "\n".join(lines)


class Partitioner:
    """
    Partitions a DAG into multiple loops based on a target dependency chain length.
    """

    def __init__(self, dag: OperatorDAG, target_chain_length: int = 8):
        """
        Args:
            dag: The operator DAG to partition.
            target_chain_length: Target number of dependent compute instructions
                                 per loop. Default 8, based on VADDS sweet spot.
        """
        self.dag = dag
        self.target_chain_length = target_chain_length

    def suggest_cut_depths(
        self,
        single_chain_base: int = 8,
        parallel_chain_base: int = 5,
    ) -> List[int]:
        """
        Heuristic cut suggestion:
        - serial zones prefer ~8 compute-depths per loop
        - parallel zones prefer ~5 compute-depths per loop
        - transitions between serial/parallel zones become natural cut candidates
        """
        topo = self.dag.topological_sort()
        compute_depth = self._get_compute_depths(topo)
        width_by_depth = self._get_compute_widths(compute_depth)

        if not width_by_depth:
            return []

        max_depth = max(width_by_depth.keys())
        cuts: Set[int] = set()

        regions: List[Tuple[int, int, bool]] = []
        region_start = 0
        region_parallel = width_by_depth.get(0, 0) >= 2

        for depth in range(1, max_depth + 1):
            is_parallel = width_by_depth.get(depth, 0) >= 2
            if is_parallel != region_parallel:
                regions.append((region_start, depth, region_parallel))
                region_start = depth
                region_parallel = is_parallel
        regions.append((region_start, max_depth + 1, region_parallel))

        for idx, (start, end, is_parallel) in enumerate(regions):
            base = parallel_chain_base if is_parallel else single_chain_base
            self._add_periodic_cuts(cuts, start, end, base)

            if idx + 1 >= len(regions):
                continue

            next_start, next_end, next_parallel = regions[idx + 1]
            cur_len = end - start
            next_len = next_end - next_start
            cur_base = parallel_chain_base if is_parallel else single_chain_base
            next_base = parallel_chain_base if next_parallel else single_chain_base

            if (
                cur_len >= 3
                and next_len >= 3
                and (cur_len >= cur_base or next_len >= next_base)
            ):
                cuts.add(next_start)

        return sorted(d for d in cuts if 0 < d <= max_depth)

    @staticmethod
    def _add_periodic_cuts(cuts: Set[int], start: int, end: int, base: int) -> None:
        if base <= 0:
            return
        depth = start + base
        while depth < end:
            cuts.add(depth)
            depth += base

    def partition_by_cut_points(self, cut_depths: List[int]) -> PartitionPlan:
        """
        Partition by cutting at specific compute-depth levels.
        Args:
            cut_depths: Sorted list of depths where a new partition starts.
                        Example: cut_depths=[8, 16] 
                        -> Partition 0: depth 0-7
                        -> Partition 1: depth 8-15
                        -> Partition 2: depth 16+
        """
        topo = self.dag.topological_sort()
        compute_depth = self._get_compute_depths(topo)
        
        sorted_cuts = sorted(cut_depths)
        
        def get_part_id(d: int) -> int:
            for i, cut in enumerate(sorted_cuts):
                if d < cut:
                    return i
            return len(sorted_cuts)

        node_to_part_id: Dict[int, int] = {}
        for nid in self.dag.nodes:
            node = self.dag.nodes[nid]
            if node.is_compute():
                node_to_part_id[nid] = get_part_id(compute_depth[nid])
        
        # Second pass: assign VLD/VST
        for nid in self.dag.nodes:
            node = self.dag.nodes[nid]
            if node.is_load():
                if node.successors:
                    first_succ = min(node.successors)
                    node_to_part_id[nid] = node_to_part_id.get(first_succ, 0)
                else:
                    node_to_part_id[nid] = 0
            elif node.is_store():
                if node.predecessors:
                    last_pred = max(node.predecessors)
                    node_to_part_id[nid] = node_to_part_id.get(last_pred, 0)
                else:
                    node_to_part_id[nid] = 0

        part_groups = defaultdict(list)
        for nid in topo:
            part_id = node_to_part_id[nid]
            part_groups[part_id].append(nid)

        partitions = []
        for pid in sorted(part_groups.keys()):
            partitions.append(Partition(pid, part_groups[pid]))

        return PartitionPlan(self.dag, partitions)

    def _get_compute_depths(self, topo: List[int]) -> Dict[int, int]:
        compute_depth = {nid: 0 for nid in self.dag.nodes}
        for nid in topo:
            node = self.dag.nodes[nid]
            for succ_id in node.successors:
                succ_node = self.dag.nodes[succ_id]
                if succ_node.is_compute():
                    new_depth = compute_depth[nid] + (1 if node.is_compute() else 0)
                    if new_depth > compute_depth[succ_id]:
                        compute_depth[succ_id] = new_depth
                else:
                    if compute_depth[nid] > compute_depth[succ_id]:
                        compute_depth[succ_id] = compute_depth[nid]
        return compute_depth

    def _get_compute_widths(self, compute_depth: Dict[int, int]) -> Dict[int, int]:
        width: Dict[int, int] = defaultdict(int)
        for nid, node in self.dag.nodes.items():
            if node.is_compute():
                width[compute_depth[nid]] += 1
        return dict(width)

    def partition_by_critical_path(self) -> PartitionPlan:
        """
        Original method refactored to use _get_compute_depths.
        """
        topo = self.dag.topological_sort()
        compute_depth = self._get_compute_depths(topo)

        node_to_part_id: Dict[int, int] = {}
        for nid in self.dag.nodes:
            node = self.dag.nodes[nid]
            if node.is_compute():
                node_to_part_id[nid] = compute_depth[nid] // self.target_chain_length
            elif node.is_load():
                if node.successors:
                    first_succ = min(node.successors)
                    node_to_part_id[nid] = compute_depth[first_succ] // self.target_chain_length
                else:
                    node_to_part_id[nid] = 0
            elif node.is_store():
                if node.predecessors:
                    last_pred = max(node.predecessors)
                    node_to_part_id[nid] = compute_depth[last_pred] // self.target_chain_length
                else:
                    node_to_part_id[nid] = 0

        part_groups = defaultdict(list)
        for nid in topo:
            part_id = node_to_part_id[nid]
            part_groups[part_id].append(nid)

        partitions = []
        for pid in sorted(part_groups.keys()):
            partitions.append(Partition(pid, part_groups[pid]))

        return PartitionPlan(self.dag, partitions)

        # 3. Group nodes into partitions
        part_groups: Dict[int, List[int]] = defaultdict(list)
        for nid in topo:
            part_id = node_to_part_id[nid]
            part_groups[part_id].append(nid)

        # 4. Create ordered partition list
        sorted_part_ids = sorted(part_groups.keys())
        # Re-index partitions from 0
        partitions = []
        for new_id, old_id in enumerate(sorted_part_ids):
            partitions.append(Partition(new_id, part_groups[old_id]))

        return PartitionPlan(self.dag, partitions)

    def partition_uniform(self, num_partitions: int) -> PartitionPlan:
        """
        Partition the DAG into exactly num_partitions partitions,
        distributing nodes as evenly as possible while respecting topological order.
        """
        topo = self.dag.topological_sort()
        chunk_size = max(1, len(topo) // num_partitions)

        partitions = []
        for i in range(num_partitions):
            start = i * chunk_size
            end = start + chunk_size if i < num_partitions - 1 else len(topo)
            node_ids = topo[start:end]
            if node_ids:
                partitions.append(Partition(i, node_ids))

        return PartitionPlan(self.dag, partitions)


class JsonGenerator:
    """
    Generates simulator-ready JSON from a PartitionPlan.
    
    Handles:
    - Auto-inserting VST at partition boundaries (producer side)
    - Auto-inserting VLD at partition boundaries (consumer side)  
    - Mapping internal node registers to reusable V0/V1 registers
    """

    def __init__(self, partition_plan: PartitionPlan, 
                 params: Dict[str, Any] = None,
                 dtype: str = "fp32",
                 loop_iters: str = "I",
                 loop_bound: int = 2):
        self.plan = partition_plan
        self.params = params or {"I": loop_bound}
        self.dtype = dtype
        self.loop_iters = loop_iters
        self.loop_bound = loop_bound
        self._global_topo_rank = {
            nid: idx for idx, nid in enumerate(self.plan.dag.topological_sort())
        }

    @staticmethod
    def _op_class(node: DagNode) -> int:
        if node.is_load():
            return 0
        if node.is_store():
            return 2
        return 1

    def _reorder_partition_nodes(self, partition: Partition) -> List[int]:
        """
        Reorder nodes inside one partition while preserving topology.

        This uses a frontier-style Kahn traversal instead of the original static
        source order. The effect is a BFS-like interleaving of sibling branches,
        which is particularly important for GeLU_poly-style DAGs.
        """
        dag = self.plan.dag
        node_set = set(partition.node_ids)
        indegree: Dict[int, int] = {}
        local_depth: Dict[int, int] = {nid: 0 for nid in partition.node_ids}

        for nid in partition.node_ids:
            indegree[nid] = sum(1 for p in dag.nodes[nid].predecessors if p in node_set)

        for nid in partition.node_ids:
            node = dag.nodes[nid]
            for succ in node.successors:
                if succ in node_set:
                    local_depth[succ] = max(
                        local_depth[succ],
                        local_depth[nid] + (1 if node.is_compute() else 0),
                    )

        ready = [nid for nid in partition.node_ids if indegree[nid] == 0]
        ordered: List[int] = []

        while ready:
            ready.sort(
                key=lambda nid: (
                    self._op_class(dag.nodes[nid]),
                    local_depth[nid],
                    -len([s for s in dag.nodes[nid].successors if s in node_set]),
                    self._global_topo_rank[nid],
                )
            )
            nid = ready.pop(0)
            ordered.append(nid)
            for succ in dag.nodes[nid].successors:
                if succ not in node_set:
                    continue
                indegree[succ] -= 1
                if indegree[succ] == 0:
                    ready.append(succ)

        if len(ordered) != len(partition.node_ids):
            return list(partition.node_ids)
        return ordered

    def generate(self) -> Dict[str, Any]:
        """
        Generate the full JSON trace dict ready for the simulator.
        """
        dag = self.plan.dag
        program = []

        # Build node-to-partition mapping
        node_to_part = {}
        for p in self.plan.partitions:
            for nid in p.node_ids:
                node_to_part[nid] = p.id

        for p_idx, partition in enumerate(self.plan.partitions):
            body_insts = []
            ordered_node_ids = self._reorder_partition_nodes(partition)

            # 1. Find which registers need VLD at the start of this partition
            #    (data from previous partitions)
            incoming_regs = set()
            for nid in ordered_node_ids:
                node = dag.nodes[nid]
                for pred_id in node.predecessors:
                    if node_to_part.get(pred_id, -1) != p_idx:
                        # This is a cross-boundary dependency
                        pred_node = dag.nodes[pred_id]
                        for d in pred_node.dst:
                            if d in node.src:
                                incoming_regs.add(d)

            # Check for original VLD nodes in this partition
            # Insert VLD for cross-boundary incoming data
            for reg in sorted(incoming_regs):
                mem_name = f"mem_inter_{reg}"
                body_insts.append({
                    "type": "inst",
                    "op": "VLDS",
                    "dst": [reg],
                    "src": [mem_name]
                })

            # 2. Add original instructions from this partition
            #    (skip original VLD/VST if we're auto-managing boundaries)
            for nid in ordered_node_ids:
                node = dag.nodes[nid]
                # Keep original VLD only if it's loading from actual memory (memA, memB etc.)
                # Keep original VST only if it's storing to actual memory (memC etc.)
                if node.is_load():
                    # Check if this is an "original" VLD (loads from mem*, not inter)
                    if any(s.startswith("mem") for s in node.src):
                        body_insts.append({
                            "type": "inst",
                            "op": node.op,
                            "dst": list(node.dst),
                            "src": list(node.src),
                        })
                elif node.is_store():
                    if any(d.startswith("mem") for d in node.dst):
                        body_insts.append({
                            "type": "inst",
                            "op": node.op,
                            "dst": list(node.dst),
                            "src": list(node.src),
                        })
                else:
                    # Compute instruction: always include
                    body_insts.append({
                        "type": "inst",
                        "op": node.op,
                        "dst": list(node.dst),
                        "src": list(node.src),
                    })

            # 3. Find which registers need VST at the end of this partition
            #    (data consumed by later partitions)
            outgoing_regs = set()
            for nid in ordered_node_ids:
                node = dag.nodes[nid]
                for succ_id in node.successors:
                    if node_to_part.get(succ_id, -1) != p_idx:
                        # Cross-boundary: need to store this node's output
                        for d in node.dst:
                            outgoing_regs.add(d)

            # Insert VST for cross-boundary outgoing data
            for reg in sorted(outgoing_regs):
                mem_name = f"mem_inter_{reg}"
                body_insts.append({
                    "type": "inst",
                    "op": "VSTS",
                    "dst": [mem_name],
                    "src": [reg]
                })

            # Build loop node
            loop_node = {
                "type": "loop",
                "iters": self.loop_iters,
                "unroll": 1,
                "body": body_insts,
            }
            program.append(loop_node)

        return {
            "dtype": self.dtype,
            "params": dict(self.params),
            "program": program,
        }

    def save(self, path: str) -> None:
        """Generate and save the JSON trace."""
        data = self.generate()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Saved partitioned trace to {path}")


# ---- CLI entry point ----
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Partition a VF trace DAG and generate simulator JSON")
    parser.add_argument("trace", help="Path to the input JSON trace file")
    parser.add_argument("--target-chain", type=int, default=8,
                        help="Target dependency chain length per loop (default: 8)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output JSON path (default: auto-named in results/)")
    parser.add_argument("--loop-bound", type=int, default=2,
                        help="Loop iteration count (default: 2)")
    args = parser.parse_args()

    # Build DAG
    dag, meta = OperatorDAG.from_json_trace(args.trace)
    print(f"Source: {meta['source_file']}")
    print(dag.summary())
    print()

    # Partition
    partitioner = Partitioner(dag, target_chain_length=args.target_chain)
    plan = partitioner.partition_by_critical_path()
    print(plan.summary())
    print()

    # Generate output JSON
    if args.output is None:
        base = os.path.splitext(os.path.basename(args.trace))[0]
        args.output = os.path.join("results", f"{base}_partitioned_chain{args.target_chain}.json")

    params = meta.get("params", {})
    if "I" not in params:
        params["I"] = args.loop_bound

    generator = JsonGenerator(
        plan,
        params=params,
        dtype=meta.get("dtype", "fp32"),
        loop_iters="I",
        loop_bound=args.loop_bound,
    )
    generator.save(args.output)

    # Quick preview
    data = generator.generate()
    total_loops = len(data["program"])
    total_insts = sum(len(loop["body"]) for loop in data["program"])
    print(f"\nGenerated {total_loops} loops with {total_insts} total instructions")
    print(f"First loop has {len(data['program'][0]['body'])} instructions")
    if total_loops > 1:
        print(f"Last loop has {len(data['program'][-1]['body'])} instructions")


if __name__ == "__main__":
    main()
