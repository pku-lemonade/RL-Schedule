import os
import sys
import json
import argparse
from typing import List, Dict, Any, Tuple
import numpy as np

import torch
from torch_geometric.data import HeteroData

from .data_loader import ManycoreDatasetBuilder, TimeWindowConfig, build_sequences
from .model_hetero import HeteroTemporalModel


class FailurePredictor:
    def __init__(self, 
                 model_path: str,
                 mesh_x: int = 4,
                 mesh_y: int = 4,
                 window_size: int = 1000000,
                 overlap: int = 0,
                 lookback: int = 2,
                 device: str = 'cuda',
                 threshold: float = 0.5):
        """
        初始化预测器
        
        Args:
            model_path: 训练好的模型权重文件路径
            mesh_x: mesh网格x维度
            mesh_y: mesh网格y维度
            window_size: 时间窗口大小
            overlap: 窗口重叠大小
            lookback: LSTM回看窗口数
            device: 运行设备 ('cuda' 或 'cpu')
            threshold: 预测阈值
        """
        self.mesh_x = mesh_x
        self.mesh_y = mesh_y
        self.lookback = lookback
        self.threshold = threshold
        
        # 设置设备
        # if device == 'cuda' and not torch.cuda.is_available():
        #     print("警告: CUDA不可用, 使用CPU")
        device = 'cpu'
        self.device = torch.device(device)
        
        # 配置时间窗口
        self.window_cfg = TimeWindowConfig()
        self.window_cfg.window_size = window_size
        self.window_cfg.overlap_size = overlap
        self.window_cfg.min_events_per_window = 1
        
        # 初始化数据构建器
        self.builder = ManycoreDatasetBuilder(mesh_x, mesh_y, "Mesh", self.window_cfg)
        
        # 加载模型
        self.model = self._load_model(model_path)
        self.model.eval()
        
        # print(f"预测器初始化完成")
        # print(f"  设备: {self.device}")
        # print(f"  模型: {model_path}")
        # print(f"  网格大小: {mesh_x}x{mesh_y}")
        # print(f"  阈值: {threshold}")
    
    def _load_model(self, model_path: str) -> HeteroTemporalModel:
        """加载训练好的模型"""
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        
        # 创建模型实例（需要知道输入维度）
        in_dims = {
            'core': 7,  # 7维特征
            'link': 7,  # 7维特征
        }
        core_count = self.mesh_x * self.mesh_y
        # 计算link数量: 网格内部连接 + DRAM连接
        # link_count = 2 * self.mesh_x * self.mesh_y - self.mesh_x - self.mesh_y + core_count
        link_count = 2 * ((self.mesh_x - 1) * self.mesh_y + self.mesh_x * (self.mesh_y - 1))
        
        model = HeteroTemporalModel(
            in_dims=in_dims,
            core_count=core_count,
            link_count=link_count
        )
        
        # 加载权重
        model.load_state_dict(torch.load(model_path, map_location=self.device))
        model = model.to(self.device)
        
        # print(f"模型加载成功: core_count={core_count}, link_count={link_count}")
        return model
    
    def predict(self, graphs, verbose: bool = True) -> Tuple[List[float], List[float], Dict[str, Any]]:
        if not graphs:
            return {'error': 'No graphs built from trace directory'}
        
        if verbose:
            print(f"构建了 {len(graphs)} 个时间窗口")
        
        # 构建序列
        sequences = build_sequences(graphs, lookback=self.lookback)
        
        # 对每个序列进行预测
        all_predictions = []

        core_fail_probs = []
        link_fail_probs = []
        
        with torch.no_grad():
            for seq_idx, seq in enumerate(sequences):
                # 移动到设备
                seq_on_device = [graph.to(self.device) for graph in seq]
                
                # 预测
                core_logits, link_logits, global_logits = self.model(seq_on_device)
                
                # 转换为概率
                core_probs = torch.sigmoid(core_logits).squeeze(-1).cpu().numpy()
                link_probs = torch.sigmoid(link_logits).squeeze(-1).cpu().numpy()
                global_prob = torch.sigmoid(global_logits).item()
                
                core_fail_probs.append(core_probs)
                link_fail_probs.append(link_probs)
                
                # 二值化预测
                core_preds = (core_probs >= self.threshold).astype(int)
                link_preds = (link_probs >= self.threshold).astype(int)
                global_pred = int(global_prob >= self.threshold)
                
                # 记录预测结果
                window_result = {
                    'window_index': seq_idx,
                    'global_failure_prob': float(global_prob),
                    'global_failure_pred': bool(global_pred),
                    'core_failures': [],
                    'link_failures': []
                }
                
                # 记录故障的core
                for core_id, (prob, pred) in enumerate(zip(core_probs, core_preds)):
                    if pred == 1:
                        window_result['core_failures'].append({
                            'core_id': int(core_id),
                            'probability': float(prob)
                        })
                
                # 记录故障的link
                for link_id, (prob, pred) in enumerate(zip(link_probs, link_preds)):
                    if pred == 1:
                        # 获取link连接的core对
                        core_pair = self.builder.mesh.link_to_core_pair.get(link_id, (-1, -1))
                        window_result['link_failures'].append({
                            'link_id': int(link_id),
                            'core_pair': core_pair,
                            'probability': float(prob)
                        })
                
                all_predictions.append(window_result)
                
                if verbose:
                    self._print_window_prediction(window_result)
        
        # 汇总结果
        summary = self._summarize_predictions(all_predictions)
        
        result = {
            'num_windows': len(graphs),
            'predictions': all_predictions,
            'summary': summary
        }
        
        if verbose:
            self._print_summary(summary)
        
        return core_fail_probs, link_fail_probs, result
    
    def predict_from_trace_dir(self, trace_dir: str, verbose: bool = True) -> Dict[str, Any]:
        """
        从trace目录进行预测
        
        Args:
            trace_dir: 包含 comm_trace.json 和 comp_trace.json 的目录
            verbose: 是否打印详细信息
            
        Returns:
            预测结果字典
        """
        if verbose:
            print(f"\n{'='*80}")
            print(f"开始预测: {trace_dir}")
            print(f"{'='*80}")
        
        # 构建图数据（不使用故障标签）
        graphs, meta = self.builder.build_from_trace_dir(trace_dir, failure_path=None)
        
        if not graphs:
            print(f"警告: 未能从 {trace_dir} 构建图数据")
            return {'error': 'No graphs built from trace directory'}
        
        if verbose:
            print(f"构建了 {len(graphs)} 个时间窗口")
        
        # 构建序列
        sequences = build_sequences(graphs, lookback=self.lookback)
        
        # 对每个序列进行预测
        all_predictions = []

        core_fail_probs = []
        link_fail_probs = []
        
        with torch.no_grad():
            for seq_idx, seq in enumerate(sequences):
                # 移动到设备
                seq_on_device = [graph.to(self.device) for graph in seq]
                
                # 预测
                core_logits, link_logits, global_logits = self.model(seq_on_device)
                
                # 转换为概率
                core_probs = torch.sigmoid(core_logits).squeeze(-1).cpu().numpy()
                link_probs = torch.sigmoid(link_logits).squeeze(-1).cpu().numpy()
                global_prob = torch.sigmoid(global_logits).item()
                
                core_fail_probs.append(core_probs)
                link_fail_probs.append(link_probs)
                
                # 二值化预测
                core_preds = (core_probs >= self.threshold).astype(int)
                link_preds = (link_probs >= self.threshold).astype(int)
                global_pred = int(global_prob >= self.threshold)
                
                # 记录预测结果
                window_result = {
                    'window_index': seq_idx,
                    'global_failure_prob': float(global_prob),
                    'global_failure_pred': bool(global_pred),
                    'core_failures': [],
                    'link_failures': []
                }
                
                # 记录故障的core
                for core_id, (prob, pred) in enumerate(zip(core_probs, core_preds)):
                    if pred == 1:
                        window_result['core_failures'].append({
                            'core_id': int(core_id),
                            'probability': float(prob)
                        })
                
                # 记录故障的link
                for link_id, (prob, pred) in enumerate(zip(link_probs, link_preds)):
                    if pred == 1:
                        # 获取link连接的core对
                        core_pair = self.builder.mesh.link_to_core_pair.get(link_id, (-1, -1))
                        window_result['link_failures'].append({
                            'link_id': int(link_id),
                            'core_pair': core_pair,
                            'probability': float(prob)
                        })
                
                all_predictions.append(window_result)
                
                if verbose:
                    self._print_window_prediction(window_result)
        
        # 汇总结果
        summary = self._summarize_predictions(all_predictions)
        
        result = {
            'trace_dir': trace_dir,
            'num_windows': len(graphs),
            'predictions': all_predictions,
            'summary': summary
        }
        
        if verbose:
            self._print_summary(summary)
        
        return core_fail_probs, link_fail_probs, result
    
    def _print_window_prediction(self, window_result: Dict[str, Any]):
        """打印单个窗口的预测结果"""
        window_idx = window_result['window_index']
        global_prob = window_result['global_failure_prob']
        global_pred = window_result['global_failure_pred']
        
        print(f"\n窗口 {window_idx}:")
        print(f"  全局故障概率: {global_prob:.4f} {'[检测到故障]' if global_pred else '[正常]'}")
        
        if window_result['core_failures']:
            print(f"  Core故障 ({len(window_result['core_failures'])} 个):")
            for failure in window_result['core_failures']:
                core_id = failure['core_id']
                prob = failure['probability']
                x, y = divmod(core_id, self.mesh_y)
                print(f"    - Core {core_id} (位置 {x},{y}): 概率 {prob:.4f}")
        
        if window_result['link_failures']:
            print(f"  Link故障 ({len(window_result['link_failures'])} 个):")
            for failure in window_result['link_failures']:
                link_id = failure['link_id']
                core_pair = failure['core_pair']
                prob = failure['probability']
                print(f"    - Link {link_id} (连接 Core {core_pair[0]} ↔ Core {core_pair[1]}): 概率 {prob:.4f}")
    
    def _summarize_predictions(self, all_predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """汇总所有窗口的预测结果"""
        num_windows = len(all_predictions)
        num_windows_with_failure = sum(1 for p in all_predictions if p['global_failure_pred'])
        
        # 统计每个core的故障频率
        core_failure_count = {}
        core_failure_max_prob = {}
        for pred in all_predictions:
            for failure in pred['core_failures']:
                core_id = failure['core_id']
                prob = failure['probability']
                core_failure_count[core_id] = core_failure_count.get(core_id, 0) + 1
                core_failure_max_prob[core_id] = max(core_failure_max_prob.get(core_id, 0), prob)
        
        # 统计每个link的故障频率
        link_failure_count = {}
        link_failure_max_prob = {}
        for pred in all_predictions:
            for failure in pred['link_failures']:
                link_id = failure['link_id']
                prob = failure['probability']
                link_failure_count[link_id] = link_failure_count.get(link_id, 0) + 1
                link_failure_max_prob[link_id] = max(link_failure_max_prob.get(link_id, 0), prob)
        
        # 按出现频率排序
        top_cores = sorted(core_failure_count.items(), key=lambda x: (-x[1], -core_failure_max_prob[x[0]]))
        top_links = sorted(link_failure_count.items(), key=lambda x: (-x[1], -link_failure_max_prob[x[0]]))
        
        summary = {
            'total_windows': num_windows,
            'windows_with_failure': num_windows_with_failure,
            'failure_rate': num_windows_with_failure / num_windows if num_windows > 0 else 0,
            'suspected_core_failures': [
                {
                    'core_id': core_id,
                    'frequency': count,
                    'max_probability': float(core_failure_max_prob[core_id])
                }
                for core_id, count in top_cores[:10]  # 只取前10个
            ],
            'suspected_link_failures': [
                {
                    'link_id': link_id,
                    'core_pair': self.builder.mesh.link_to_core_pair.get(link_id, (-1, -1)),
                    'frequency': count,
                    'max_probability': float(link_failure_max_prob[link_id])
                }
                for link_id, count in top_links[:10]  # 只取前10个
            ]
        }
        
        return summary
    
    def _print_summary(self, summary: Dict[str, Any]):
        """打印汇总结果"""
        print(f"\n{'='*80}")
        print("预测汇总")
        print(f"{'='*80}")
        print(f"总窗口数: {summary['total_windows']}")
        print(f"检测到故障的窗口数: {summary['windows_with_failure']}")
        print(f"故障率: {summary['failure_rate']:.2%}")
        
        if summary['suspected_core_failures']:
            print(f"\n疑似Core故障 (按出现频率排序):")
            for item in summary['suspected_core_failures']:
                core_id = item['core_id']
                x, y = divmod(core_id, self.mesh_y)
                print(f"  - Core {core_id} (位置 {x},{y}): "
                      f"出现 {item['frequency']} 次, "
                      f"最高概率 {item['max_probability']:.4f}")
        
        if summary['suspected_link_failures']:
            print(f"\n疑似Link故障 (按出现频率排序):")
            for item in summary['suspected_link_failures']:
                link_id = item['link_id']
                core_pair = item['core_pair']
                print(f"  - Link {link_id} (连接 Core {core_pair[0]} ↔ Core {core_pair[1]}): "
                      f"出现 {item['frequency']} 次, "
                      f"最高概率 {item['max_probability']:.4f}")
        
        print(f"{'='*80}\n")
    
    def save_results(self, results: Dict[str, Any], output_path: str):
        """保存预测结果到JSON文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"预测结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='对模拟器trace数据进行实时故障预测')
    parser.add_argument('--model', type=str, required=True,
                       help='训练好的模型权重文件路径 (例如: best_model.pth)')
    parser.add_argument('--trace_dir', type=str, required=True,
                       help='trace数据目录, 包含 comm_trace.json 和 comp_trace.json')
    parser.add_argument('--output', type=str, default=None,
                       help='输出结果JSON文件路径 (可选)')
    parser.add_argument('--mesh_x', type=int, default=4,
                       help='Mesh网格x维度 (默认: 4)')
    parser.add_argument('--mesh_y', type=int, default=4,
                       help='Mesh网格y维度 (默认: 4)')
    parser.add_argument('--window_size', type=int, default=5000000,
                       help='时间窗口大小 (默认: 1000000)')
    parser.add_argument('--overlap', type=int, default=0,
                       help='窗口重叠大小 (默认: 200000)')
    parser.add_argument('--lookback', type=int, default=2,
                       help='LSTM回看窗口数 (默认: 2)')
    parser.add_argument('--threshold', type=float, default=0.5,
                       help='预测阈值 (默认: 0.5)')
    parser.add_argument('--device', type=str, default='cuda',
                       choices=['cuda', 'cpu'],
                       help='运行设备 (默认: cuda)')
    parser.add_argument('--quiet', action='store_true',
                       help='静默模式，不打印详细信息')
    
    args = parser.parse_args()
    
    # 创建预测器
    predictor = FailurePredictor(
        model_path=args.model,
        mesh_x=args.mesh_x,
        mesh_y=args.mesh_y,
        window_size=args.window_size,
        overlap=args.overlap,
        lookback=args.lookback,
        device=args.device,
        threshold=args.threshold
    )
    
    # 进行预测
    core_probs, link_probs, results = predictor.predict_from_trace_dir(args.trace_dir, verbose=not args.quiet)
    
    print("Core:")
    print(core_probs)
    print("Link:")
    print(link_probs)

    # 保存结果
    if args.output:
        predictor.save_results(results, args.output)
    
    # 返回是否检测到故障
    has_failure = results['summary']['windows_with_failure'] > 0
    sys.exit(0 if has_failure else 1)


if __name__ == '__main__':
    main()

