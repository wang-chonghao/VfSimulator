"""
DAG Visualization Tool for VF Operators.
Generates PNG images or Mermaid.js markup from JSON traces.
"""

import os
import sys
import argparse
from collections import defaultdict
import matplotlib.pyplot as plt
import networkx as nx
from typing import Dict, List, Any

# Add project root and optimizer to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "optimizer"))

try:
    from dag_lib import OperatorDAG
except ImportError:
    print("Error: Could not find dag_lib.py in optimizer/ directory.")
    sys.exit(1)

def get_node_color(node):
    if node.is_load():
        return "#FFCC00"  # Gold for Memory Load
    if node.is_store():
        return "#66CC66"  # Green for Memory Store
    return "#6699FF"      # Blue for Compute


def _compute_layered_positions(dag, node_ids: List[int]) -> Dict[int, List[float]]:
    """
    Build a deterministic layered layout to keep medium DAGs readable.

    We first assign each node a depth via longest-path layering, then spread nodes
    vertically within the same layer. A second pass biases each node toward the
    average y-position of its already-placed predecessors to reduce crossings.
    """
    topo = dag.topological_sort()
    depth = {nid: 0 for nid in dag.nodes}
    for nid in topo:
        for succ in dag.nodes[nid].successors:
            depth[succ] = max(depth[succ], depth[nid] + 1)

    layers: Dict[int, List[int]] = defaultdict(list)
    node_id_set = set(node_ids)
    for nid in topo:
        if nid in node_id_set:
            layers[depth[nid]].append(nid)

    y_hint: Dict[int, float] = {}
    for layer_nodes in layers.values():
        for nid in layer_nodes:
            preds = [p for p in dag.nodes[nid].predecessors if p in y_hint]
            if preds:
                y_hint[nid] = sum(y_hint[p] for p in preds) / len(preds)
            else:
                y_hint[nid] = 0.0
        layer_nodes.sort(key=lambda nid: (y_hint[nid], nid))

    positions: Dict[int, List[float]] = {}
    max_layer_size = max((len(nodes) for nodes in layers.values()), default=1)
    vertical_step = 1.6 if max_layer_size <= 8 else 1.2
    horizontal_step = 3.2

    for layer_idx, layer_nodes in sorted(layers.items()):
        count = len(layer_nodes)
        center = (count - 1) / 2.0
        for offset, nid in enumerate(layer_nodes):
            x = layer_idx * horizontal_step
            y = (center - offset) * vertical_step
            positions[nid] = [x, y]

    return positions

def visualize_dag(trace_path: str, output_path: str = None, max_nodes: int = 150):
    dag, meta = OperatorDAG.from_json_trace(trace_path)
    G = nx.DiGraph()
    
    node_ids = sorted(list(dag.nodes.keys()))
    if len(node_ids) > max_nodes:
        print(f"Warning: Graph has {len(node_ids)} nodes. Truncating to first {max_nodes} for readability.")
        node_ids = node_ids[:max_nodes]
    
    # 1. Add all nodes and edges first
    for nid in node_ids:
        G.add_node(nid)
    
    for nid in node_ids:
        for succ in dag.nodes[nid].successors:
            if succ in node_ids:
                G.add_edge(nid, succ)
    
    # 2. Build labels and colors in the exact order G.nodes() returns
    labels = {}
    colors = []
    for nid in G.nodes():
        node = dag.nodes[nid]
        # Label: ID + Op + Dst
        dst_str = f"->{','.join(node.dst)}" if node.dst else ""
        labels[nid] = f"{nid}\n{node.op}\n{dst_str}"
        colors.append(get_node_color(node))
    
    # Calculate dynamic sizing
    num_drawn = len(G.nodes())
    fig_width = max(14, num_drawn * 0.8)
    fig_height = max(8, num_drawn * 0.35)
    node_size = max(700, 3200 - num_drawn * 18)
    font_size = max(6, 10 - num_drawn // 40)

    plt.figure(figsize=(fig_width, fig_height))

    pos = _compute_layered_positions(dag, list(G.nodes()))

    edge_width = 1.8 if num_drawn <= 40 else 1.5
    arrow_size = 16 if num_drawn <= 40 else 14
    nx.draw(
        G,
        pos,
        labels=labels,
        with_labels=True,
        node_color=colors,
        node_size=node_size,
        font_size=font_size,
        node_shape='s',
        edge_color='#7A7A7A',
        width=edge_width,
        alpha=0.95,
        arrowsize=arrow_size,
        arrowstyle='-|>',
        connectionstyle='arc3,rad=0.08',
    )

    plt.title(f"Operator DAG: {os.path.basename(trace_path)} ({len(G.nodes())} nodes)", 
              fontsize=16, fontweight='bold')
    
    if output_path:
        plt.savefig(output_path, bbox_inches='tight', dpi=120)
        print(f"Visualization saved to: {output_path}")
    else:
        plt.show()

def export_mermaid(trace_path: str, output_path: str = None):
    dag, meta = OperatorDAG.from_json_trace(trace_path)
    lines = ["graph TD"]
    
    # Global styles
    lines.append("  classDef load fill:#f9f,stroke:#333,stroke-width:2px;")
    lines.append("  classDef store fill:#bbf,stroke:#333,stroke-width:2px;")
    lines.append("  classDef compute fill:#fff,stroke:#333,stroke-width:1px;")

    for nid, node in dag.nodes.items():
        node_label = f"{nid}:{node.op}"
        if node.dst:
            node_label += f" [{','.join(node.dst)}]"
            
        # Shape coding
        if node.is_load():
            shape = f"{nid}[(\"{node_label}\")]"
            style = "load"
        elif node.is_store():
            shape = f"{nid}[[\"{node_label}\"]]"
            style = "store"
        else:
            shape = f"{nid}(\"{node_label}\")"
            style = "compute"
            
        lines.append(f"  {shape}::: {style}")
        
        for succ in node.successors:
            lines.append(f"  {nid} --> {succ}")
            
    mermaid_str = "\n".join(lines)
    
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(mermaid_str)
        print(f"Mermaid markup saved to: {output_path}")
    else:
        print("\n--- Mermaid.js Markup ---\n")
        print(mermaid_str)
        print("\n-------------------------\n")
    return mermaid_str

def main():
    parser = argparse.ArgumentParser(description="Visualize Operator DAG from JSON trace")
    parser.add_argument("trace", help="Path to JSON trace file")
    parser.add_argument("--output", "-o", help="Output image path (e.g. results/dag.png)")
    parser.add_argument("--mermaid", action="store_true", help="Output Mermaid.js markup instead of PNG")
    parser.add_argument("--max-nodes", type=int, default=150, help="Max nodes to draw in PNG")
    
    args = parser.parse_args()
    
    if args.mermaid:
        output_md = args.output if args.output else os.path.join("results", f"{os.path.basename(args.trace)}.mmd")
        export_mermaid(args.trace, output_md)
    else:
        output_png = args.output if args.output else os.path.join("results", f"{os.path.basename(args.trace)}.png")
        visualize_dag(args.trace, output_png, args.max_nodes)

if __name__ == "__main__":
    main()
