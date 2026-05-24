import json
import argparse
import os
import numpy as np
import matplotlib.pyplot as plt

def main():
    parser = argparse.ArgumentParser(description="Plot IPC curves from simulator logs.")
    parser.add_argument("--log", type=str, default="results/done_by_cycle.json", help="Path to done_by_cycle.json")
    parser.add_argument("--window", type=int, default=10, help="Moving average window size (cycles)")
    parser.add_argument("--out", type=str, default="results/ipc_curves.png", help="Output PNG file")
    args = parser.parse_args()

    # Load data
    if not os.path.exists(args.log):
        # Fallback to local if results/ is not found or vice versa
        alternative_path = "done_by_cycle.json" if "results/" in args.log else os.path.join("results", args.log)
        if os.path.exists(alternative_path):
            args.log = alternative_path
        else:
            print(f"Error: Log file {args.log} not found.")
            return

    events = []
    with open(args.log, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))

    if not events:
        print("No events found in log.")
        return

    # Find total cycles
    max_cycle = 0
    for e in events:
         if e["cy"] > max_cycle:
             max_cycle = e["cy"]
    
    max_cycle += 1 

    # Count instructions per cycle
    vld_counts = np.zeros(max_cycle)
    vst_counts = np.zeros(max_cycle)
    comp_counts = np.zeros(max_cycle)

    for e in events:
        cy = e["cy"]
        op = e["op"]
        if op == "VLD":
            vld_counts[cy] += 1
        elif op == "VST":
            vst_counts[cy] += 1
        else:
            comp_counts[cy] += 1

    # Apply moving average
    w = args.window
    kernel = np.ones(w) / w
    
    # Use 'same' to keep array length equal to max_cycle
    vld_ipc = np.convolve(vld_counts, kernel, mode='same')
    vst_ipc = np.convolve(vst_counts, kernel, mode='same')
    comp_ipc = np.convolve(comp_counts, kernel, mode='same')

    cycles = np.arange(max_cycle)

    # Plot
    plt.rcParams.update({'font.size': 14})
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(cycles, comp_ipc, label=f"Compute IPC (MA={w})", color="blue", alpha=0.8, linewidth=1.5)
    ax.plot(cycles, vld_ipc, label=f"VLD IPC (MA={w})", color="green", alpha=0.8, linewidth=1.5)
    ax.plot(cycles, vst_ipc, label=f"VST IPC (MA={w})", color="red", alpha=0.8, linewidth=1.5)

    ax.set_title(f"Instruction Per Cycle (IPC) over Time\nMoving Average Window: {w} cycles", fontsize=20)
    ax.set_xlabel("Cycle", fontsize=20)
    ax.set_ylabel("IPC", fontsize=20)
    ax.set_ylim(0, 2.3)
    ax.tick_params(axis='both', labelsize=20)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(fontsize=20)
    plt.tight_layout()

    plt.savefig(args.out, dpi=300)
    print(f"Saved IPC plot to {args.out}")

if __name__ == "__main__":
    main()
