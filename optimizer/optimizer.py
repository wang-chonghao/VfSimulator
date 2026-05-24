"""
Simulated Annealing Optimizer for VF Loop Fusion.
Searches for the optimal loop partitioning points to minimize total cycles.
"""

import os
import json
import random
import math
import subprocess
import re
import time
from typing import List, Tuple, Dict, Any
import argparse

# Import from current directory
import sys
sys.path.insert(0, os.path.dirname(__file__))
from dag_lib import OperatorDAG
from partitioner import Partitioner, JsonGenerator

class SAOptimizer:
    def __init__(self, trace_path: str, 
                 initial_temp: float = 50.0, 
                 min_temp: float = 1.0, 
                 alpha: float = 0.85,
                 iters_per_temp: int = 4,
                 target_chain_base: int = 8,
                 parallel_chain_base: int = 5,
                 python_exe: str = "python"):
        """
        Args:
            trace_path: Input JSON trace.
            initial_temp: Initial SA temperature.
            min_temp: Ending temperature.
            alpha: Cooling rate.
            iters_per_temp: Iterations at each temperature step.
            target_chain_base: Initial heuristic chain length (e.g. 8 for VADDS, 5 for branched).
            python_exe: Path to python executable for simulation.
        """
        self.trace_path = trace_path
        self.dag, self.meta = OperatorDAG.from_json_trace(trace_path)
        self.max_depth = self.dag.critical_path_length()
        self.python_exe = python_exe
        
        self.t_init = initial_temp
        self.t_min = min_temp
        self.alpha = alpha
        self.iters_per_temp = iters_per_temp
        self.target_chain_base = target_chain_base
        self.parallel_chain_base = parallel_chain_base
        
        self.tmp_json = os.path.join("results", "sa_optimizer_tmp.json")
        self.tmp_out_dir = os.path.join("results", "sa_eval_tmp")
        os.makedirs("results", exist_ok=True)
        
        self.history = []
        self.best_cycles = float('inf')
        self.best_cuts = []

    def get_initial_state(self) -> List[int]:
        """Generate initial cuts using serial/parallel sweet-spot heuristics."""
        partitioner = Partitioner(self.dag)
        cuts = partitioner.suggest_cut_depths(
            single_chain_base=self.target_chain_base,
            parallel_chain_base=self.parallel_chain_base,
        )
        if cuts:
            return cuts

        cuts = []
        for d in range(self.target_chain_base, self.max_depth, self.target_chain_base):
            cuts.append(d)
        return sorted(list(set(cuts)))

    def perturb(self, cuts: List[int]) -> List[int]:
        """Mutate the state: add, remove, or shift a cut point."""
        new_cuts = list(cuts)
        # Probability weights for mutation types
        # If no cuts, must add. If max_depth is small, limited movement.
        dice = random.random()
        
        if not new_cuts:
            # Must add
            d = random.randint(1, self.max_depth - 1)
            new_cuts.append(d)
        elif dice < 0.2: 
            # Add a cut
            d = random.randint(1, self.max_depth - 1)
            if d not in new_cuts:
                new_cuts.append(d)
        elif dice < 0.4:
            # Remove a cut
            idx = random.randint(0, len(new_cuts) - 1)
            new_cuts.pop(idx)
        else:
            # Shift a cut
            idx = random.randint(0, len(new_cuts) - 1)
            delta = random.choice([-2, -1, 1, 2])
            new_d = new_cuts[idx] + delta
            if 1 <= new_d <= self.max_depth - 1 and new_d not in new_cuts:
                new_cuts[idx] = new_d
                    
        return sorted(list(set(new_cuts)))

    def evaluate(self, cuts: List[int]) -> int:
        """Measure the cycles for a given set of cut points."""
        partitioner = Partitioner(self.dag)
        plan = partitioner.partition_by_cut_points(cuts)
        
        # Prepare parameters for sim
        params = self.meta.get("params", {"I": 2})
        # Note: we use loop_bound from meta if available
        generator = JsonGenerator(
            plan, 
            params=params,
            dtype=self.meta.get("dtype", "fp32")
        )
        generator.save(self.tmp_json)
        
        # Run simulator
        cmd = [
            self.python_exe,
            "main.py",
            "--trace",
            self.tmp_json,
            "--out_dir",
            self.tmp_out_dir,
        ]
        try:
            # Using timeout to prevent hangs
            result = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=30)
            match = re.search(r"cycles_executed = (\d+)", result)
            if match:
                return int(match.group(1))
        except Exception as e:
            # print(f"[Error] Failed to evaluate cuts {cuts}: {e}")
            return 1000000  # High penalty
        return 1000000

    def optimize(self):
        print(f"\n[SA] Starting optimization for {self.trace_path}")
        print(f"[SA] Max compute depth: {self.max_depth}")
        print(f"[SA] Initial heuristic base: {self.target_chain_base}")
        
        curr_cuts = self.get_initial_state()
        curr_cycles = self.evaluate(curr_cuts)
        
        self.best_cuts = list(curr_cuts)
        self.best_cycles = curr_cycles
        
        print(f"[SA] Initial Cuts: {curr_cuts}, Cycles: {curr_cycles}")
        
        t = self.t_init
        step = 0
        
        while t > self.t_min:
            for _ in range(self.iters_per_temp):
                next_cuts = self.perturb(curr_cuts)
                next_cycles = self.evaluate(next_cuts)
                
                delta = next_cycles - curr_cycles
                
                # Metropolis Criterion
                if delta < 0:
                    accepted = True
                else:
                    try:
                        prob = math.exp(-delta / t)
                    except OverflowError:
                        prob = 0
                    accepted = random.random() < prob
                
                if accepted:
                    curr_cuts = next_cuts
                    curr_cycles = next_cycles
                    
                    if curr_cycles < self.best_cycles:
                        self.best_cycles = curr_cycles
                        self.best_cuts = list(curr_cuts)
                        print(f"[*] Step {step:4d} | Temp {t:6.2f} | NEW BEST: {self.best_cycles} cycles | Cuts: {self.best_cuts}")
                
                self.history.append({
                    "step": step,
                    "temp": t,
                    "cycles": curr_cycles,
                    "best": self.best_cycles,
                    "cuts": list(curr_cuts)
                })
                step += 1
                
            t *= self.alpha
            # print(f"Progress: Temp={t:.2f}, Cycles={curr_cycles}")

        print(f"\n[SA] Optimization complete!")
        print(f"[SA] Best Cycles: {self.best_cycles}")
        print(f"[SA] Best Cuts:   {self.best_cuts}")
        
        # Save final result
        final_file = os.path.join("results", f"{os.path.basename(self.trace_path).replace('.json', '')}_optimized.json")
        partitioner = Partitioner(self.dag)
        plan = partitioner.partition_by_cut_points(self.best_cuts)
        generator = JsonGenerator(plan, params=self.meta.get("params", {"I": 2}))
        generator.save(final_file)
        
        # Save history for analysis
        with open(os.path.join("results", "sa_history.json"), "w") as f:
            json.dump(self.history, f, indent=2)
            
        return self.best_cuts, self.best_cycles

def main():
    parser = argparse.ArgumentParser(description="Simulated Annealing Optimizer for VF Fusion")
    parser.add_argument("trace", help="Input JSON trace")
    parser.add_argument("--base", type=int, default=8, help="Initial heuristic chain length (default: 8)")
    parser.add_argument("--parallel-base", type=int, default=5, help="Initial heuristic chain length for parallel regions (default: 5)")
    parser.add_argument("--temp", type=float, default=50.0, help="Initial temperature (default: 50.0)")
    parser.add_argument("--iters", type=int, default=5, help="Iterations per temp step (default: 5)")
    parser.add_argument("--alpha", type=float, default=0.9, help="Cooling rate (default: 0.9)")
    parser.add_argument("--python", type=str, default="D:\\miniconda3\\envs\\vfsim\\python.exe", 
                        help="Path to python executable")
    
    args = parser.parse_args()
    
    optimizer = SAOptimizer(
        trace_path=args.trace,
        initial_temp=args.temp,
        iters_per_temp=args.iters,
        alpha=args.alpha,
        target_chain_base=args.base,
        parallel_chain_base=args.parallel_base,
        python_exe=args.python
    )
    
    start_time = time.time()
    best_cuts, best_cycles = optimizer.optimize()
    end_time = time.time()
    
    print(f"\nSearch Time: {end_time - start_time:.2f}s")

if __name__ == "__main__":
    main()
