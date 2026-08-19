import math
import simpy
from .config import DMAEngineConfig


class DMAEngine:
    """Generic multi-channel DMA engine for GM/DDR WDMA/RDMA nodes.

    Each channel can transfer data independently.  *clock_scale* scales
    the per-cycle bandwidth to model clock-domain crossing (e.g. DDR
    domain at 1150 MHz vs ACI domain at 1125 MHz).
    """

    def __init__(self, env, config: DMAEngineConfig, clock_scale: float = 1.0):
        self.env = env
        self.width = float(config.width) * clock_scale
        self.channel_store = simpy.Store(env, capacity=config.channels)
        for i in range(config.channels):
            self.channel_store.put(i)
        self.dispatch_interval = config.dispatch_interval
        self.dispatch = simpy.Resource(env, capacity=1)

    def transfer(self, data_bytes: int):
        if self.dispatch_interval:
            with self.dispatch.request() as req:
                yield req
                yield self.env.timeout(self.dispatch_interval)
        ch = yield self.channel_store.get()
        try:
            yield self.env.timeout(math.ceil(data_bytes / self.width))
        finally:
            yield self.channel_store.put(ch)
