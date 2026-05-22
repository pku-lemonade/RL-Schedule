import json
from pathlib import Path
from types import SimpleNamespace

from utils.mapper import NetworkMapper, parse_mapping

ROOT = Path('/home/wjc/thermal')
MAPPING = ROOT / 'workloads' / 'darknet19-4-4.json'
NODES_OUT = ROOT / 'logs' / 'layer18_layer19_dfg_nodes.txt'
EDGES_OUT = ROOT / 'logs' / 'layer18_layer19_cross_edges.txt'


def ds_list(tensor_slice):
    return [(d.start, d.end) for d in tensor_slice] if tensor_slice else []


def make_trace():
    cores = []
    for cid in range(16):
        slow = 0.2
        util = 0.4
        if cid == 14:
            slow = 0.9
            util = 0.9
        elif cid == 7:
            slow = 0.1
            util = 0.1
        cores.append(SimpleNamespace(id=cid, slow=slow, ultilization=util))
    return SimpleNamespace(time_slices=[SimpleNamespace(cores=cores)])


def main():
    mapper = NetworkMapper(parse_mapping(str(MAPPING)))
    trace = make_trace()
    report1 = mapper.apply_local_remap(18, 'remove', 2, 0, trace)
    report2 = mapper.apply_local_remap(18, 'shift', 14, 7, trace)
    layer18 = mapper.layer_node_ranges[18]
    layer19 = mapper.layer_node_ranges[19]
    l18_set = set(range(layer18[0], layer18[1] + 1))
    l19_set = set(range(layer19[0], layer19[1] + 1))

    header = {
        'remove_report': report1.to_dict() if hasattr(report1, 'to_dict') else report1,
        'shift_report': report2.to_dict() if hasattr(report2, 'to_dict') else report2,
        'layer18_range': layer18,
        'layer19_range': layer19,
        'layer18_node_count': layer18[1] - layer18[0] + 1,
        'layer19_node_count': layer19[1] - layer19[0] + 1,
    }

    with NODES_OUT.open('w') as fh:
        fh.write(json.dumps(header, indent=2))
        fh.write('\n\n')
        for label, layer_range, own_set, other_set in [
            ('LAYER18', layer18, l18_set, l19_set),
            ('LAYER19', layer19, l19_set, l18_set),
        ]:
            fh.write(f'## {label} nodes {layer_range[0]}..{layer_range[1]}\n')
            for idx in range(layer_range[0], layer_range[1] + 1):
                node = mapper.dfg.nodes[idx]
                fathers = [
                    {
                        'index': f,
                        'op': mapper.dfg.nodes[f].operation.name,
                        'core': mapper.dfg.nodes[f].core_id,
                        'from_layer': 18 if f in l18_set else 19 if f in l19_set else 'other',
                    }
                    for f in node.father
                ]
                sons = [
                    {
                        'index': s,
                        'op': mapper.dfg.nodes[s].operation.name,
                        'core': mapper.dfg.nodes[s].core_id,
                        'to_layer': 18 if s in l18_set else 19 if s in l19_set else 'other',
                    }
                    for s in node.son
                ]
                fh.write(json.dumps({
                    'index': idx,
                    'op': node.operation.name,
                    'core': node.core_id,
                    'input': ds_list(node.input_size),
                    'weight': ds_list(node.weight_size),
                    'output': ds_list(node.output_size),
                    'fathers': fathers,
                    'sons': sons,
                }, ensure_ascii=False))
                fh.write('\n')
            fh.write('\n')

    with EDGES_OUT.open('w') as fh:
        fh.write(json.dumps(header, indent=2))
        fh.write('\n\n## Cross edges layer18 -> layer19\n')
        for idx in range(layer18[0], layer18[1] + 1):
            node = mapper.dfg.nodes[idx]
            for s in node.son:
                if s in l19_set:
                    dst = mapper.dfg.nodes[s]
                    fh.write(json.dumps({
                        'from_index': idx,
                        'from_op': node.operation.name,
                        'from_core': node.core_id,
                        'from_output': ds_list(node.output_size),
                        'to_index': s,
                        'to_op': dst.operation.name,
                        'to_core': dst.core_id,
                        'to_input': ds_list(dst.input_size),
                    }, ensure_ascii=False))
                    fh.write('\n')

    print(NODES_OUT)
    print(EDGES_OUT)


if __name__ == '__main__':
    main()
