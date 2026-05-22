import numpy as np
import math


class RewardCalculator:
    def __init__(self, alpha = 2.0, beta_core = 1.0, beta_link = 0.5):
        self.alpha = alpha
        self.beta_core = beta_core
        self.beta_link = beta_link
        
        self.baseline_cycles = None


    def reset(self, initial_cycles):
        self.baseline_cycles = initial_cycles

        if self.baseline_cycles == 0:
            RuntimeError("Baseline cycles can't be zero.")


    def calculate(self, cycles=None, trace=None, legal_move=True):
        # illegal action punishment
        if not legal_move:
            return -5.0, {}

        current_cycles = cycles

        # performance reward
        r_perf = 1.0 * (self.baseline_cycles - current_cycles) / self.baseline_cycles

        # thermal reward
        termal_stats = self._analyze_thermal_distribution(trace_data=trace)
        
        avg_core_cv = termal_stats['avg_core_cv']
        r_core_therm = -1.0 * avg_core_cv 
        
        # to be adjusted
        avg_link_cv = termal_stats['avg_link_cv']
        r_link_therm = -1.0 * avg_link_cv

        total_reward = (self.alpha * r_perf) + \
                       (self.beta_core * r_core_therm) + \
                       (self.beta_link * r_link_therm)

        info = {
            "r_perf": r_perf,
            "r_core": r_core_therm,
            "r_link": r_link_therm,
            "raw_cycles": current_cycles,
            "raw_core_cv": avg_core_cv,
            "raw_link_cv": avg_link_cv
        }
        
        return total_reward, info


    def _analyze_thermal_distribution(self, trace_data):
        slices = trace_data.time_slices
        num_slices = len(slices)
        
        if num_slices == 0:
            return {'avg_core_cv': 0.0, 'avg_link_cv': 0.0}

        total_core_cv = 0.0
        total_link_cv = 0.0
        
        num_cores = len(slices[0].cores)

        for timeslice in slices:
            # core thermal distribution
            core_loads = np.zeros(num_cores)
            
            for item in timeslice.cores:
                core_id = item.id
                op_cnt = item.op_num
                
                if core_id is not None and 0 <= core_id < num_cores:
                    core_loads[core_id] += op_cnt
            
            core_mean = np.mean(core_loads)
            core_std = np.std(core_loads)
            
            if core_mean > 1e-6:
                total_core_cv += (core_std / core_mean)
            else:
                total_core_cv += 0.0

            # link thermal distribution
            link_loads = []
            for item in timeslice.links:
                link_loads.append(item.op_num)
            
            if len(link_loads) > 0:
                link_arr = np.array(link_loads)
                link_mean = np.mean(link_arr)
                link_std = np.std(link_arr)
                
                if link_mean > 1e-6:
                    total_link_cv += (link_std / link_mean)
                else:
                    total_link_cv += 0.0
            else:
                total_link_cv += 0.0

        return {
            'avg_core_cv': total_core_cv / num_slices,
            'avg_link_cv': total_link_cv / num_slices
        }
