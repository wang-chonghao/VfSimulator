import os
import subprocess
import re
import json
import matplotlib.pyplot as plt

def main():
    # Use the specific conda python interpreter provided by the user
    python_exe = r"D:\miniconda3\envs\vfsim\python.exe"
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_dir = os.path.join(base_dir, "VFtest", "vadd_fusion_tests")
    results_root = os.path.join(base_dir, "results", "fusion_sweep")
    os.makedirs(results_root, exist_ok=True)

    # 1. Find and sort test files
    files = [f for f in os.listdir(test_dir) if f.startswith("VADD_fusion_") and f.endswith(".json")]
    
    # Extract number of loops from filename to sort correctly
    # Filename format: VADD_fusion_Xloops_Yvadds.json
    def get_num_loops(f):
        match = re.search(r"fusion_(\d+)loops", f)
        return int(match.group(1)) if match else 0

    files.sort(key=get_num_loops)

    results = []

    print(f"{'Loops':<10} | {'VADDS/Loop':<15} | {'Cycles':<10}")
    print("-" * 45)

    for f in files:
        num_loops = get_num_loops(f)
        vadds_match = re.search(r"(\d+)vadds", f)
        vadds_per_loop = int(vadds_match.group(1)) if vadds_match else 0
        
        trace_path = os.path.join(test_dir, f)
        out_subdir = os.path.join(results_root, f.replace(".json", ""))
        
        # 2. Execute main.py
        cmd = [python_exe, os.path.join(base_dir, "main.py"), "--trace", trace_path, "--out_dir", out_subdir]
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=base_dir)
        stdout, _ = process.communicate()
        
        # 3. Extract cycles executed
        # Pattern: Done. cycles_executed = XXX
        cycle_match = re.search(r"cycles_executed\s*=\s*(\d+)", stdout)
        cycles = int(cycle_match.group(1)) if cycle_match else None
        
        if cycles is not None:
            results.append({
                "loops": num_loops,
                "vadds_per_loop": vadds_per_loop,
                "cycles": cycles,
                "filename": f
            })
            print(f"{num_loops:<10} | {vadds_per_loop:<15} | {cycles:<10}")
        else:
            print(f"{num_loops:<10} | {vadds_per_loop:<15} | Failed to capture results")
            # print(stdout) # Debug if needed

    # 4. Save raw results
    with open(os.path.join(results_root, "sweep_results.json"), "w") as jf:
        json.dump(results, jf, indent=4)

    # 5. Plotting
    if results:
        loops = [r["loops"] for r in results]
        cycles = [r["cycles"] for r in results]

        plt.figure(figsize=(10, 6))
        plt.plot(loops, cycles, marker='o', linestyle='-', color='b')
        
        # Annotate points
        for i, val in enumerate(cycles):
            plt.annotate(f"{val}", (loops[i], cycles[i]), textcoords="offset points", xytext=(0,10), ha='center')

        plt.xscale('log', base=10) # Use log scale for loops as they go 1, 2, 4... 512
        plt.title("VF Fusion Sweet Spot Exploration: Loops vs Total Cycles")
        plt.xlabel("Number of Split Loops (Log Scale)")
        plt.ylabel("Total Execution Cycles")
        plt.grid(True, which="both", ls="-", alpha=0.5)
        
        plot_path = os.path.join(results_root, "fusion_sweet_spot.png")
        plt.savefig(plot_path)
        print(f"\nSaved comparison plot to {plot_path}")
        
        # Find the best case
        best_case = min(results, key=lambda x: x["cycles"])
        print(f"Sweet Spot Identified: {best_case['loops']} loops ({best_case['vadds_per_loop']} vadds/loop) with {best_case['cycles']} cycles.")

if __name__ == "__main__":
    main()
