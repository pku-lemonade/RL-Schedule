from types import SimpleNamespace
from simulator.architecture import Arch
from simulator.run import arch_analyzer, fail_analyzer
from utils.mapper import NetworkMapper, parse_mapping

mapper = NetworkMapper(parse_mapping('/home/wjc/thermal/workloads/darknet19-4-4.json'))
trace = SimpleNamespace(time_slices=[SimpleNamespace(cores=[SimpleNamespace(id=i, slow=(0.9 if i==0 else 0.1 if i==4 else 0.2), ultilization=(0.9 if i==0 else 0.1 if i==4 else 0.4)) for i in range(16)])])
print('replace', mapper.apply_local_remap(0,'replace',0,4,trace).to_dict(), flush=True)
print('shift', mapper.apply_local_remap(0,'shift',0,4,trace).to_dict(), flush=True)
arch = Arch(arch_analyzer('/home/wjc/thermal/configs/instances/gemini4_4.json'), mapper, fail_analyzer('/home/wjc/thermal/configs/instances/normal.json'))
result = arch.execute()
print('done', result.now, flush=True)
