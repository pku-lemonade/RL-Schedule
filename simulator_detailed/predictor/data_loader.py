import json
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional

import numpy as np
import torch
from torch_geometric.data import HeteroData

from ..utils.definitions import NoCChannel
from .topology import Mesh, parse_fabric_id

w_link, w_core = 1.0, 0.2  # 可调超参

@dataclass
class TimeWindowConfig:
    window_size: int = 1_000_000
    overlap_size: int = 200_000
    min_events_per_window: int = 1


def _load_json(path: str) -> Any:
    with open(path, 'r') as f:
        return json.load(f)


def _split_windows(comm_events: List[Dict[str, Any]],
                   comp_events: List[Dict[str, Any]],
                   cfg: TimeWindowConfig) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int]]:
    if not comm_events and not comp_events:
        return []
    mins = []
    maxs = []
    if comm_events:
        mins.append(min(int(e['start_time']) for e in comm_events))
        maxs.append(max(int(e['end_time']) for e in comm_events))
    if comp_events:
        mins.append(min(int(e['start_time']) for e in comp_events))
        maxs.append(max(int(e['end_time']) for e in comp_events))
    start = min(mins)
    end = max(maxs)

    # print(f"window: {start}-{end}, size: {cfg.window_size}")

    windows = []
    cur = start
    while cur <= end:
        w_end = cur + cfg.window_size
        comm_in = [e for e in comm_events if min(e['end_time'], w_end) > max(e['start_time'], cur)]
        comp_in = [e for e in comp_events if min(e['end_time'], w_end) > max(e['start_time'], cur)]
        if len(comm_in) + len(comp_in) >= cfg.min_events_per_window:
            windows.append((comm_in, comp_in, cur, w_end))
        cur = w_end - cfg.overlap_size
    return windows


def _safe_mean(values: List[float]) -> float:
    return float(np.mean(values)) if values else 0.0


class ManycoreDatasetBuilder:
    def __init__(self,
                 x: int,
                 y: int,
                 noc_type: str,
                 window_cfg: TimeWindowConfig):
        if noc_type == 'Mesh':
            self.mesh = Mesh(x, y)
        self.window_cfg = window_cfg

    def _calculate_link_endpoint(self, router_id: int, direction: int) -> int:
        rx = router_id % self.mesh.x
        ry = router_id // self.mesh.x
        match direction:
            case 0:  # NORTH (y+1)
                if ry >= self.mesh.y - 1:
                    return -1
                return router_id + self.mesh.x
            case 1:  # SOUTH (y-1)
                if ry <= 0:
                    return -1
                return router_id - self.mesh.x
            case 2:  # EAST (x+1)
                if rx >= self.mesh.x - 1:
                    return -1
                return router_id + 1
            case 3:  # WEST (x-1)
                if rx <= 0:
                    return -1
                return router_id - 1
            case _:
                return -1

    def _get_link_id_from_core_pair(
        self,
        core1: int,
        core2: int,
        fabric_id: NoCChannel,
    ) -> int:
        key = (fabric_id, core1, core2)
        return self.mesh.to_link_index.get(key, -1)
    

    def build_from_trace(self,
                         comp_events: List[Dict[str, Any]],
                         comm_events: List[Dict[str, Any]],
                         failure_path: Optional[str] = None) -> Tuple[List[HeteroData], Dict[str, Any]]:
        # Expected files: comm_trace.json, comp_trace.json
        # comm_path = os.path.join(trace_dir, 'comm_trace.json')
        # comp_path = os.path.join(trace_dir, 'comp_trace.json')
        # tmp = _load_json(comm_path) if os.path.exists(comm_path) else []
        # comm_events = tmp['trace']
        # tmp = _load_json(comp_path) if os.path.exists(comp_path) else []
        # comp_events = tmp['trace']

        windows = _split_windows(comm_events, comp_events, self.window_cfg)

        # Optional failure annotations
        failures = _load_json(failure_path) if (failure_path and os.path.exists(failure_path)) else None

        hetero_graphs: List[HeteroData] = []
        meta: Dict[str, Any] = {
            'core_count': self.mesh.core_count,
            'link_count': self.mesh.link_count,
        }

        for i, (comm_win, comp_win, window_start, window_end) in enumerate(windows):
            # print (comm_win)
            data = self._build_window_graph(comm_win, comp_win)
            data = self._apply_failure_labels(data, failures, window_start, window_end)
            
            # Debug: Print label information after applying failure labels
            # print(f"    [DATA_LOADER DEBUG] Window {i} (time: {window_start} - {window_end}):")
            if hasattr(data['core'], 'y') and data['core'].y is not None:
                core_labels = data['core'].y.squeeze(-1).numpy()
                num_failed_cores = int(core_labels.sum())
                failed_core_indices = [idx for idx, label in enumerate(core_labels) if label == 1]
                # print(f"      Core: {len(core_labels)} nodes, {num_failed_cores} failures", end="")
                if num_failed_cores > 0:
                    print(f" at indices {failed_core_indices}")
                else:
                    print()
            else:
                pass
                # print(f"      Core: No labels (y attribute missing or None)")
            
            if hasattr(data['link'], 'y') and data['link'].y is not None:
                link_labels = data['link'].y.squeeze(-1).numpy()
                num_failed_links = int(link_labels.sum())
                failed_link_indices = [idx for idx, label in enumerate(link_labels) if label == 1]
                print(f"      Link: {len(link_labels)} nodes, {num_failed_links} failures", end="")
                if num_failed_links > 0:
                    print(f" at indices {failed_link_indices}")
                else:
                    print()
            else:
                pass
                # print(f"      Link: No labels (y attribute missing or None)")
            
            # Also print failure info if available
            if failures is not None:
                print(f"      Failure data loaded: Yes")
                if 'tpu' in failures:
                    print(f"        TPU failures: {len(failures['tpu'])} entries")
                if 'link' in failures:
                    print(f"        Link failures: {len(failures['link'])} entries")
                if 'router' in failures:
                    print(f"        Router failures: {len(failures['router'])} entries")
                if 'lsu' in failures:
                    print(f"        LSU failures: {len(failures['lsu'])} entries")
            else:
                pass
                # print(f"      Failure data loaded: No (failures=None)")
            
            hetero_graphs.append(data)
            

        return hetero_graphs, meta


    def build_from_trace_dir(self,
                             trace_dir: str,
                             failure_path: Optional[str] = None) -> Tuple[List[HeteroData], Dict[str, Any]]:
        # Expected files: comm_trace.json, comp_trace.json
        comm_path = os.path.join(trace_dir, 'comm_trace.json')
        comp_path = os.path.join(trace_dir, 'comp_trace.json')
        tmp = _load_json(comm_path) if os.path.exists(comm_path) else {}
        comm_events = tmp.get('trace', [])
        tmp = _load_json(comp_path) if os.path.exists(comp_path) else {}
        comp_events = tmp.get('trace', [])

        windows = _split_windows(comm_events, comp_events, self.window_cfg)

        # Optional failure annotations
        failures = _load_json(failure_path) if (failure_path and os.path.exists(failure_path)) else None

        hetero_graphs: List[HeteroData] = []
        meta: Dict[str, Any] = {
            'core_count': self.mesh.core_count,
            'link_count': self.mesh.link_count,
        }

        for i, (comm_win, comp_win, window_start, window_end) in enumerate(windows):
            # print (comm_win)
            data = self._build_window_graph(comm_win, comp_win)
            data = self._apply_failure_labels(data, failures, window_start, window_end)
            
            # Debug: Print label information after applying failure labels
            print(f"    [DATA_LOADER DEBUG] Window {i} (time: {window_start} - {window_end}):")
            if hasattr(data['core'], 'y') and data['core'].y is not None:
                core_labels = data['core'].y.squeeze(-1).numpy()
                num_failed_cores = int(core_labels.sum())
                failed_core_indices = [idx for idx, label in enumerate(core_labels) if label == 1]
                print(f"      Core: {len(core_labels)} nodes, {num_failed_cores} failures", end="")
                if num_failed_cores > 0:
                    print(f" at indices {failed_core_indices}")
                else:
                    print()
            else:
                print(f"      Core: No labels (y attribute missing or None)")
            
            if hasattr(data['link'], 'y') and data['link'].y is not None:
                link_labels = data['link'].y.squeeze(-1).numpy()
                num_failed_links = int(link_labels.sum())
                failed_link_indices = [idx for idx, label in enumerate(link_labels) if label == 1]
                print(f"      Link: {len(link_labels)} nodes, {num_failed_links} failures", end="")
                if num_failed_links > 0:
                    print(f" at indices {failed_link_indices}")
                else:
                    print()
            else:
                print(f"      Link: No labels (y attribute missing or None)")
            
            # Also print failure info if available
            if failures is not None:
                print(f"      Failure data loaded: Yes")
                if 'tpu' in failures:
                    print(f"        TPU failures: {len(failures['tpu'])} entries")
                if 'link' in failures:
                    print(f"        Link failures: {len(failures['link'])} entries")
                if 'router' in failures:
                    print(f"        Router failures: {len(failures['router'])} entries")
                if 'lsu' in failures:
                    print(f"        LSU failures: {len(failures['lsu'])} entries")
            else:
                print(f"      Failure data loaded: No (failures=None)")
            
            hetero_graphs.append(data)
            

        return hetero_graphs, meta

    def _build_window_graph(self,
                            comm_win: List[Dict[str, Any]],
                            comp_win: List[Dict[str, Any]]) -> HeteroData:
        mesh = self.mesh
        window_len = float(self.window_cfg.window_size)

        # Initialize features (7-dim by assumption per user spec list)
        core_feat = np.zeros((mesh.core_count, 7), dtype=np.float32)
        link_feat = np.zeros((mesh.link_count, 7), dtype=np.float32)
        # Aggregate comp inst per core
        comp_by_core: Dict[int, List[Dict[str, Any]]] = {}
        for inst in comp_win:
            pe = int(inst.get('pe_id', -1))
            if pe < 0 or pe >= mesh.core_count:
                continue
            comp_by_core.setdefault(pe, []).append(inst)

        for pe_id, insts in comp_by_core.items():
            if not insts:
                continue
            flops = [float(i.get('flops', 0.0)) for i in insts]
            durations = [max(0.0, float(i.get('end_time', 0.0)) - float(i.get('start_time', 0.0))) for i in insts]
            rate = [f / d for f, d in zip(flops, durations) if d > 0]
            total = float(len(insts))
            core_feat[pe_id, 0] = float(np.max(flops)) if flops else 0.0 # 最大浮点运算量
            core_feat[pe_id, 1] = float(np.sum(flops)) # 浮点运算量总和
            core_feat[pe_id, 2] = float(np.max(rate)) if rate else 0.0 # 最大平均浮点运算率
            core_feat[pe_id, 3] = _safe_mean(rate) # 平均浮点运算率
            core_feat[pe_id, 4] = float(np.max(durations)) if durations else 0.0 # 最大执行时间
            core_feat[pe_id, 5] = _safe_mean(durations) # 平均执行时间
            core_feat[pe_id, 6] = total / window_len if window_len > 0 else 0.0 # 指令数除以窗口长度
        #print(core_feat)
        # COMM: attach to traversed links/cores
        # Relations derived from a single comm inst:
        # 保留的边类型：link-link (同comm), core-link (同comm)
        link_link_src: List[int] = []
        link_link_dst: List[int] = []
        core_link_src: List[int] = []
        core_link_dst: List[int] = []
        
        # 用于累积link的详细统计信息
        link_stats: Dict[int, Dict[str, List[float]]] = {i: {
            'sizes': [], 
            'durations': [], 
            'hops': [],
            'per_hop_delays': [],
            'throughputs': []
        } for i in range(mesh.link_count)}

        for inst in comm_win:
            src = int(inst.get('src_id', -1))
            dst = int(inst.get('dst_id', -1))
            fabric_id = parse_fabric_id(
                inst.get('fabric_id', NoCChannel.CH0)
            )
            size = float(inst.get('data_size', 0.0))
            duration = max(0.0, float(inst.get('end_time', 0.0)) - float(inst.get('start_time', 0.0)))

            traversed: List[Tuple[str, int]] = []
            num_hops = 0
            
            if src == -1 and dst >= 0:
                # DRAM -> core
                key = (fabric_id, mesh.core_count, dst)
                if key in mesh.to_link_index:
                    traversed.append(('link', mesh.to_link_index[key]))
                    num_hops = 1
            elif dst == -1 and src >= 0:
                key = (fabric_id, src, mesh.core_count)
                if key in mesh.to_link_index:
                    traversed.append(('link', mesh.to_link_index[key]))
                    num_hops = 1
            elif 0 <= src < mesh.core_count and 0 <= dst < mesh.core_count:
                traversed = mesh.manhattan_path_nodes(src, dst, fabric_id)
                # 计算hop数（只计算link）
                num_hops = sum(1 for kind, _ in traversed if kind == 'link')

            touched_links: List[int] = []
            touched_cores: List[int] = []
            for kind, idx in traversed:
                if kind == 'link':
                    touched_links.append(idx)
                else:
                    touched_cores.append(idx)

            # 基于通信模型：tcomm = ts + l*th + m*tw
            # 对于每个经过的link，我们记录统计信息
            if touched_links and duration > 0:
                # 平均每hop延迟（粗略估计）
                per_hop_delay = duration / max(1, num_hops)
                # 有效吞吐率（考虑所有hop的平均）
                throughput = size / duration if duration > 0 else 0.0
                
                for kind, idx in traversed:
                    if kind == 'link':
                        link_stats[idx]['sizes'].append(size)
                        link_stats[idx]['durations'].append(duration)
                        link_stats[idx]['hops'].append(float(num_hops))
                        link_stats[idx]['per_hop_delays'].append(per_hop_delay)
                        link_stats[idx]['throughputs'].append(throughput)
            
            #同次 comm：link-link 全连接（双向）
            for i in range(len(touched_links)):
                for j in range(i + 1, len(touched_links)):
                    link_link_src.append(touched_links[i]); link_link_dst.append(touched_links[j])
                    link_link_src.append(touched_links[j]); link_link_dst.append(touched_links[i])
                link_link_src.append(touched_links[i]); link_link_dst.append(touched_links[i])

            # 同次 comm：core-link 连接（只保留core到link方向）
            for l in touched_links:
                for c in touched_cores:
                    core_link_src.append(c); core_link_dst.append(l)  # core -> link
        
        # 基于通信模型计算link特征
        # 特征设计（基于 tcomm = ts + l*th + m*tw）：
        # [0] 最大数据量
        # [1] 总数据量
        # [2] 平均吞吐率 (总数据量/总时间)
        # [3] 最大吞吐率（瞬时峰值）
        # [4] 平均每hop延迟
        # [5] 延迟标准差 (反映拥塞/不稳定性)
        # [6] 通信密度 (指令数/窗口长度)
        
        for link_id, stats in link_stats.items():
            if len(stats['sizes']) > 0:
                sizes = np.array(stats['sizes'])
                durations = np.array(stats['durations'])
                throughputs = np.array(stats['throughputs'])
                per_hop_delays = np.array(stats['per_hop_delays'])
                
                # [0] 最大数据量
                link_feat[link_id, 0] = float(np.max(sizes))
                
                # [1] 总数据量
                link_feat[link_id, 1] = float(np.sum(sizes))
                
                # [2] 平均吞吐率 = 总数据量 / 总时间
                total_data = np.sum(sizes)
                total_time = np.sum(durations)
                link_feat[link_id, 2] = float(total_data / total_time) if total_time > 0 else 0.0
                
                # [3] 最大吞吐率（瞬时峰值）
                link_feat[link_id, 3] = float(np.max(throughputs))
                
                # [4] 平均每hop延迟
                link_feat[link_id, 4] = float(np.mean(per_hop_delays))
                
                # [5] 延迟标准差（反映拥塞程度，故障时延迟会变化）
                if len(per_hop_delays) > 1:
                    link_feat[link_id, 5] = float(np.std(per_hop_delays))
                else:
                    link_feat[link_id, 5] = 0.0
                
                # [6] 通信密度 = 指令数 / 窗口长度
                link_feat[link_id, 6] = float(len(stats['sizes'])) / window_len if window_len > 0 else 0.0
        
        #print("Link features computed based on communication model")
        #print(link_feat)

        data = HeteroData()
        data['core'].x = torch.tensor(core_feat, dtype=torch.float32)
        data['link'].x = torch.tensor(link_feat, dtype=torch.float32)

        # Physical relations
        if self.mesh.core_link[0]:
            data['core', 'core_to_link', 'link'].edge_index = torch.tensor(self.mesh.core_link, dtype=torch.long)
        else:
            data['core', 'core_to_link', 'link'].edge_index = torch.empty((2, 0), dtype=torch.long)
        if self.mesh.link_core[0]:
            data['link', 'link_to_core', 'core'].edge_index = torch.tensor(self.mesh.link_core, dtype=torch.long)
        else:
            data['link', 'link_to_core', 'core'].edge_index = torch.empty((2, 0), dtype=torch.long)

        # Comm-derived relations
        def _to_edge_index(src: List[int], dst: List[int]) -> torch.Tensor:
            if not src:
                return torch.empty((2, 0), dtype=torch.long)
            return torch.tensor([src, dst], dtype=torch.long)

        data['link', 'same_comm_link', 'link'].edge_index = _to_edge_index(link_link_src, link_link_dst)
        data['core', 'same_comm_to_link', 'link'].edge_index = _to_edge_index(core_link_src, core_link_dst)

        
        return data

    def _apply_failure_labels(self, data: HeteroData, failures: Optional[Dict], 
                         window_start_time: float, window_end_time: float):
        if failures is None:
            return data

        core_labels = torch.zeros((self.mesh.core_count, 1), dtype=torch.float32)
        link_labels = torch.zeros((self.mesh.link_count, 1), dtype=torch.float32)

        # if_match = False
        link_match = False
        tpu_match = False
        # 处理link故障
        if 'link' in failures:
            for item in failures['link']:
                fail_start = item.get('start_time', 0)
                fail_end = item.get('end_time', float('inf'))
                router_id = item.get('router_id', -1)
                direction = item.get('direction', -1)
                fabric_id = parse_fabric_id(
                    item.get('fabric_id', NoCChannel.CH0)
                )
                print("link failure", fail_start, fail_end)
                # 检查故障时间是否与当前时间窗口重叠
                if (window_start_time < fail_end and window_end_time > fail_start and 
                    router_id >= 0 and direction >= 0):
                    # print("link failure match", fail_start, fail_end)
                    if_match = True
                    # 计算link的终点core
                    dst_core = self._calculate_link_endpoint(router_id, direction)
                    print("router_id", router_id)
                    print("direction", direction)
                    print("dst_core", dst_core)
                    if dst_core >= 0:
                        # 获取link id并设置标签
                        link_id = self._get_link_id_from_core_pair(
                            router_id,
                            dst_core,
                            fabric_id,
                        )
                        # print("link_id", link_id)
                        if link_id >= 0:
                            link_match = True
                            link_labels[link_id, 0] = 1.0
                    
                        

        # 处理tpu故障（core故障）
        if 'tpu' in failures:
            for item in failures['tpu']:
                fail_start = item.get('start_time', 0)
                fail_end = item.get('end_time', float('inf'))
                pe_id = item.get('pe_id', -1)
                
                # 检查故障时间是否与当前时间窗口重叠
                if (window_start_time < fail_end and window_end_time > fail_start and 
                    0 <= pe_id < self.mesh.core_count):
                    tpu_match = True
                    print("tpu failure match", fail_start, fail_end)
                    print("pe_id", pe_id)
                    core_labels[pe_id, 0] = 1.0

        # 处理router故障（影响所有连接到该router的links）
        if 'router' in failures:
            for item in failures['router']:
                fail_start = item.get('start_time', 0)
                fail_end = item.get('end_time', float('inf'))
                router_id = item.get('router_id', -1)
                fabric_id = parse_fabric_id(
                    item.get('fabric_id', NoCChannel.CH0)
                )
                
                # 检查故障时间是否与当前时间窗口重叠
                if (window_start_time < fail_end and window_end_time > fail_start and 
                    0 <= router_id < self.mesh.core_count):
                    
                    # router故障影响该core连接的所有links
                    # 这里需要遍历所有与该core相关的links
                    for link_id in range(self.mesh.link_count):
                        if self.mesh.link_to_fabric[link_id] is not fabric_id:
                            continue
                        core1, core2 = self.mesh.link_to_core_pair[link_id]
                        if core1 == router_id or core2 == router_id:
                            link_labels[link_id, 0] = 1.0

        # 处理lsu故障（类似tpu）
        if 'lsu' in failures:
            for item in failures['lsu']:
                fail_start = item.get('start_time', 0)
                fail_end = item.get('end_time', float('inf'))
                pe_id = item.get('pe_id', -1)
                
                # 检查故障时间是否与当前时间窗口重叠
                if (window_start_time < fail_end and window_end_time > fail_start and 
                    0 <= pe_id < self.mesh.core_count):
                    core_labels[pe_id, 0] = 1.0
        # if window_start_time > 149004186 and window_end_time < 512077530:
        #     print(core_labels)
        #     print(link_labels)
        # print(failures)
        # 设置标签
        data['core'].y = core_labels
        data['link'].y = link_labels
        # if if_match:
        #     print(link_labels)
        # if link_match:
        #     print("link match", link_labels)
        # if tpu_match:
        #     print("tpu match", core_labels)
        return data

def build_sequences(graphs: List[HeteroData], lookback: int = 2) -> List[List[HeteroData]]:
    # Build sequences of length lookback+1 (previous windows + current)
    seqs: List[List[HeteroData]] = []
    for i in range(len(graphs)):
        start = max(0, i - lookback)
        seq = graphs[start:i + 1]
        # Pad from the left by repeating the earliest
        if len(seq) < lookback + 1:
            seq = [seq[0]] * (lookback + 1 - len(seq)) + seq
        seqs.append(seq)
    return seqs
