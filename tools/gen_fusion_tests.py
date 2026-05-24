import json
import os

def generate_vf_fusion_tests(output_dir=os.path.join("VFtest", "vadd_fusion_tests"), total_vadds=1024, loop_bound=2):
    """
    Generates JSON test files representing different levels of VF loop fusion.
    Total VADDs = 1024.
    Since loop_bound = 2, the number of VADD operations per loop body = total_vadds / loop_bound = 512.
    
    We will split these 512 dependent VADDs into `num_loops` parallel sibling loops.
    Each sibling loop will have `vadds_per_loop` = 512 / num_loops.
    
    The dependencies: VADD_i depends on VADD_{i-1}.
    VADD_0 depends on V0 and V1.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    total_body_vadds = total_vadds // loop_bound  # 512
    
    # Possible numbers of parallel loops (must be divisors of 512)
    # 1 (Deepest fusion) -> 512 (Shallowest fusion)
    num_loops_list = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
    
    for num_loops in num_loops_list:
        vadds_per_loop = total_body_vadds // num_loops
        
        program = []
        global_vadd_idx = 0
        
        current_src = "V0" 
        next_dst = "V1"
        
        for loop_idx in range(num_loops):
            body_insts = []
            
            # If this is the very first loop, load from memA. 
            # Otherwise, load from the intermediate memory block written by the previous loop.
            if loop_idx == 0:
                body_insts.append({"type": "inst", "op": "VLD", "dst": ["V0"], "src": ["memA"]})
            else:
                body_insts.append({"type": "inst", "op": "VLD", "dst": ["V0"], "src": [f"mem_inter_{loop_idx-1}"]})
            
            current_src = "V0" 
            next_dst = "V1"
            
            # Add VADDS for this loop
            for _ in range(vadds_per_loop):
                body_insts.append({
                    "type": "inst", 
                    "op": "VADDS", 
                    "dst": [next_dst], 
                    "src": [current_src]
                })
                current_src, next_dst = next_dst, current_src
                global_vadd_idx += 1
                
            # Store the result. If it's the last loop, store to memC, otherwise store to intermediate memory.
            if loop_idx == num_loops - 1:
                body_insts.append({"type": "inst", "op": "VST", "dst": ["memC"], "src": [current_src]})
            else:
                body_insts.append({"type": "inst", "op": "VST", "dst": [f"mem_inter_{loop_idx}"], "src": [current_src]})
                
            loop_node = {
                "type": "loop",
                "iters": "I",
                "unroll": 1,
                "body": body_insts
            }
            program.append(loop_node)
            
        json_data = {
            "dtype": "fp32",
            "params": {
                "I": loop_bound
            },
            "program": program
        }
        
        filename = f"VADD_fusion_{num_loops}loops_{vadds_per_loop}vadds.json"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2)
            
        print(f"Generated {filepath}: {num_loops} parallel loops, {vadds_per_loop} VADDS per loop.")

if __name__ == "__main__":
    generate_vf_fusion_tests()
