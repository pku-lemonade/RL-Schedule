import simpy
import logging
import contextlib
import math
from typing import Any, Dict, Optional, Tuple

from .definitions import (
    Direction, Message, Event, TransType, TransferMode,
    UnboundLocalPortError, UnsupportedTransferMode, ProfilingSimError,
)
from .config import RouterConfig, LinkConfig
from .distribution import NoCDist

logger = logging.getLogger("NoC")


BURST_BEATS = {0: 1, 1: 2, 3: 4, 7: 8}


class Link:
    def __init__(self, env, config: LinkConfig, capacity=1,
                 deterministic: bool = False, burst_bubble: int = 1):
        self.env = env
        self.delay = config.delay
        self.bandwidth = config.width
        self.store = simpy.Store(env, capacity=capacity)
        self.busy = simpy.PriorityResource(env, capacity=1)
        self.para_dist = NoCDist(
            shape=self.bandwidth * 0.5, rate=0.5,
            deterministic=deterministic)
        self.burst_bubble = burst_bubble
        self.events = []
        self.index2id = {}
        self.src_id = self.dst_id = -1
        self.src_port = self.dst_port = -1
        self.tag = None

    def len(self):
        return len(self.store.items)

    def bind(self, src_id, src_port, dst_id, dst_port, tag):
        self.src_id = src_id
        self.src_port = src_port
        self.dst_id = dst_id
        self.dst_port = dst_port
        self.tag = tag
        self.corefrom = src_id
        self.coreto = dst_id
        self.corefromid = src_id
        self.coretoid = dst_id

    def _payload_chunks(self, msg):
        payload = msg.byte_size()
        if msg.burst_len_mode == -1:
            return [payload]
        beat_bytes = BURST_BEATS[msg.burst_len_mode] * self.bandwidth
        chunks = []
        rem = payload
        while rem > 0:
            chunks.append(min(rem, beat_bytes))
            rem -= beat_bytes
        return chunks

    def calc_latency(self, msg):
        var_bw = self.para_dist.generate()
        if (msg.transfer_mode == TransferMode.SINGLE_SIDE
                and not msg.is_control):
            var_bw = var_bw * 0.9
        chunks = self._payload_chunks(msg)
        n_bursts = len(chunks)
        for i, payload_bytes in enumerate(chunks):
            tx_bytes = payload_bytes + (msg.header_bytes if i == 0 else 0)
            if i == 0:
                tx_bytes += msg.imm_bytes
            tx_time = math.ceil(tx_bytes / var_bw)
            with self.busy.request(priority=-msg.priority) as req:
                yield req
                event_index = len(self.events)
                self.events.append(Event(
                    index=msg.index,
                    start_time=int(round(self.env.now)),
                    src_id=self.src_id, dst_id=self.dst_id,
                    src_port=self.src_port, dst_port=self.dst_port,
                    data_size=payload_bytes,
                    is_control=msg.is_control,
                    is_sync=bool(msg.sync),
                    reduce_count=msg.reduce_count,
                    priority=msg.priority,
                    burst_index=i,
                    burst_count=n_bursts))
                latency = tx_time + (self.delay if i == 0 else 0)
                yield self.env.timeout(latency)
                self.events[event_index].end_time = int(round(self.env.now))
            if i + 1 < n_bursts:
                yield self.env.timeout(self.burst_bubble)
        self.index2id[msg.index] = len(self.events) - 1
        yield self.store.put(msg)

    def put(self, msg):
        return self.env.process(self.calc_latency(msg))

    def get(self):
        return self.store.get()


class Router:
    def __init__(self, env, config: RouterConfig, id: int, dim_x: int, dim_y: int,
                 per_hop_time=1, start_up_time=1, local_inj_cap: int = 0):
        self.env = env
        self.vc = config.vc
        self.routing_algorithm = config.type
        self.id = id
        self.dim_x = dim_x
        self.dim_y = dim_y
        self.per_hop_time = per_hop_time
        self.start_up_time = start_up_time
        self.chip_rank = 0
        self.egress_table = {}

        self.links = {
            Direction.NORTH: {'in': None, 'out': None},
            Direction.SOUTH: {'in': None, 'out': None},
            Direction.EAST:  {'in': None, 'out': None},
            Direction.WEST:  {'in': None, 'out': None},
        }
        self.local_ports: Dict[int, Dict[str, Any]] = {}
        self.node_router: Optional[Dict[int, int]] = None
        self.reduce_latency = config.reduce_latency
        self.reduce_stores: Dict[int, Any] = {}
        self.reduce_active: set = set()
        self.reduce_trees: Optional[Dict[int, Any]] = None
        self.fifos: Optional[Dict[Tuple[int, int], Any]] = None
        cap = local_inj_cap if local_inj_cap and local_inj_cap > 0 else 10 ** 6
        self.local_inj_cap = cap
        self.local_inj = simpy.Resource(env, capacity=cap)
        self.env.process(self.run())

    def bind_link(self, direction, link_in, link_out):
        self.links[direction]['in'] = link_in
        self.links[direction]['out'] = link_out

    def bind_local_port(self, port: int, link_in, link_out):
        if port < 0 or port > 22:
            raise ValueError(f"local port {port} out of range")
        if port in self.local_ports:
            raise ValueError(f"local port {port} already bound on router {self.id}")
        self.local_ports[port] = {'in': link_in, 'out': link_out}

    def local_in(self):
        return self.local_ports.get(self._delivery_port(), {}).get('in')

    def _delivery_port(self):
        return 0

    def route(self, msg, next_dir):
        out = self.links[next_dir]['out']
        if out is None:
            raise UnboundLocalPortError(
                f"router {self.id} has no output for direction {next_dir}")
        yield out.put(msg)

    def route_local(self, msg, port: int):
        lp = self.local_ports.get(port)
        if lp is None or lp['out'] is None:
            raise UnboundLocalPortError(
                f"router {self.id} has no bound local port {port}")
        yield lp['out'].put(msg)

    def resolve_router(self, node_id: int) -> int:
        if self.node_router is not None and node_id in self.node_router:
            return self.node_router[node_id]
        return node_id

    def routing(self, msg, from_local: bool, in_port=None):
        if msg.trans_type == TransType.REDUCE:
            self._handle_reduce(msg)
            return
        if msg.trans_type in (TransType.MULTICAST, TransType.BROADCAST):
            mask = msg.dst_mask
            if msg.trans_type == TransType.BROADCAST and not mask:
                mask = (1 << (self.dim_x * self.dim_y)) - 1
            if mask:
                self.route_multicast(msg, mask)
            else:
                logger.warning("router %s received multicast with empty mask",
                               self.id)
            return
        target = self.resolve_router(msg.dst)
        cross = msg.dst_rank != self.chip_rank
        if cross:
            if msg.sync and not msg.is_control:
                raise ProfilingSimError(
                    f"cross-chip outer sync is unsupported "
                    f"(dst_rank={msg.dst_rank})")
            if msg.dst_rank not in self.egress_table:
                raise ProfilingSimError(
                    f"no egress AdaLink for remote rank {msg.dst_rank} "
                    f"on chip {self.chip_rank}")
            target, _eg_port = self.egress_table[msg.dst_rank]
        single = (msg.transfer_mode == TransferMode.SINGLE_SIDE
                  and not msg.is_control and not cross)
        do_sync = bool(msg.sync) and not msg.is_control

        if single and from_local:
            if 0 <= msg.src < self.dim_x * self.dim_y and 0 <= msg.dst < self.dim_x * self.dim_y:
                raise UnsupportedTransferMode(
                    "single-side transfer between two PEs is unsupported")
            if target != self.id:
                next_dir = self.calculate_next_router(target)
                out = self.links[next_dir]['out']
                if out is not None:
                    out.put(self._control_msg(msg, msg.dst, msg.src, req=True))

        if do_sync and from_local and target != self.id:
            rel_dir = self.calculate_next_router(target)
            rel_out = self.links[rel_dir]['out']
            if rel_out is not None:
                rel_out.put(self._sync_msg(msg, msg.dst, msg.src, to_dst=True))

        if from_local:
            yield self.env.process(self._fifo_acquire(msg))
            if msg.layout is not None:
                overhead = msg.layout.endpoint_overhead_cycles()
                if overhead:
                    yield self.env.timeout(overhead)

        next_dir = None
        if target != self.id:
            if msg.trans_type == TransType.FIXPATH and msg.fixed_path:
                next_dir = self._fixed_path_next(msg)
            else:
                next_dir = self.calculate_next_router(target)

        uses_local_bus = from_local or target == self.id
        inj_cm = None
        if uses_local_bus and not msg.is_control:
            inj_cm = self.local_inj.request()
            yield inj_cm
        try:
            if target == self.id:
                delivery_port = (self.egress_table[msg.dst_rank][1]
                                 if cross else msg.dst_local_port)
                yield self.env.process(self.route_local(msg, delivery_port))
                yield self.env.process(self._fifo_return(msg))
                if cross:
                    return
                if single:
                    src_router = self.resolve_router(msg.src)
                    if src_router != self.id:
                        back_dir = self.calculate_next_router(src_router)
                        back = self.links[back_dir]['out']
                        if back is not None:
                            back.put(self._control_msg(msg, msg.src, msg.dst,
                                                       req=False))
                if do_sync:
                    src_router = self.resolve_router(msg.src)
                    if src_router != self.id:
                        acq_dir = self.calculate_next_router(src_router)
                        acq_out = self.links[acq_dir]['out']
                        if acq_out is not None:
                            acq_out.put(self._sync_msg(msg, msg.src, msg.dst,
                                                       to_dst=False))
            else:
                yield self.env.timeout(self.per_hop_time)
                yield self.env.process(self.route(msg, next_dir))
        finally:
            if inj_cm is not None:
                self.local_inj.release(inj_cm)

    def _fixed_path_next(self, msg):
        path = msg.fixed_path
        try:
            idx = path.index(self.id)
        except ValueError as exc:
            raise ProfilingSimError(
                f"router {self.id} not on FIXPATH {path}") from exc
        if idx + 1 >= len(path):
            raise ProfilingSimError(
                f"FIXPATH {path} has no next hop after router {self.id}")
        nxt = path[idx + 1]
        return self.calculate_next_router(nxt)

    @staticmethod
    def _control_msg(msg, dst, src, req: bool):
        from .definitions import DimSlice
        return msg.model_copy(update={
            'dst': dst, 'src': src,
            'data': [DimSlice(start=0, end=16)],
            'element_bytes': 1, 'header_bytes': 0,
            'transfer_mode': TransferMode.DUAL_SIDE,
            'is_control': True, 'dst_mask': 0,
            'trans_type': TransType.SINGLECAST,
            'dst_local_port': msg.src_local_port if not req else msg.dst_local_port,
            'priority': 0, 'burst_len_mode': -1,
            'fifo_hw_id': -1, 'fifo_logic_id': -1, 'fifo_check_type': -1,
            'layout': None,
        })

    @staticmethod
    def _sync_msg(msg, dst, src, to_dst: bool):
        from .definitions import DimSlice
        return msg.model_copy(update={
            'dst': dst, 'src': src,
            'data': [DimSlice(start=0, end=16)],
            'element_bytes': 1, 'header_bytes': 0,
            'transfer_mode': TransferMode.DUAL_SIDE,
            'is_control': True, 'sync': True, 'dst_mask': 0,
            'trans_type': TransType.SINGLECAST,
            'dst_local_port': (msg.dst_local_port if to_dst
                               else msg.src_local_port),
            'priority': 0, 'burst_len_mode': -1,
            'fifo_hw_id': -1, 'fifo_logic_id': -1, 'fifo_check_type': -1,
            'layout': None,
        })

    def _fifo_acquire(self, msg):
        if (not msg.is_control
                and msg.trans_type == TransType.SINGLECAST
                and msg.fifo_check_type == 0):
            key = (msg.fifo_hw_id, msg.fifo_logic_id)
            if self.fifos is None or key not in self.fifos:
                raise ProfilingSimError(
                    f"WRITE_FULL references unregistered FIFO {key}")
            yield self.fifos[key]['credits'].get(1)

    def _fifo_return(self, msg):
        if (not msg.is_control
                and msg.trans_type == TransType.SINGLECAST
                and msg.fifo_check_type in (0, 2)):
            key = (msg.fifo_hw_id, msg.fifo_logic_id)
            if self.fifos is None or key not in self.fifos:
                raise ProfilingSimError(
                    f"FIFO return references unregistered FIFO {key}")
            fifo = self.fifos[key]
            if fifo['credits'].level < fifo['depth']:
                yield fifo['credits'].put(1)
            else:
                logger.debug("FIFO %s credit return dropped (already full)",
                             key)

    def route_multicast(self, msg, mask=None):
        if mask is None:
            mask = msg.dst_mask
        mask = mask & ((1 << (self.dim_x * self.dim_y)) - 1)
        if not mask:
            logger.warning("router %s received multicast with empty mask",
                           self.id)
            return
        delivered = False
        if mask & (1 << self.id):
            self.env.process(self.route_local(msg, msg.dst_local_port))
            delivered = True
            if msg.sync and not msg.is_control:
                src_router = self.resolve_router(msg.src)
                if src_router != self.id:
                    acq_dir = self.calculate_next_router(src_router)
                    acq_out = self.links[acq_dir]['out']
                    if acq_out is not None:
                        acq_out.put(self._sync_msg(msg, msg.src, msg.dst,
                                                   to_dst=False))
        for direction, submask in self._multicast_branches(mask):
            if submask:
                out = self.links[direction]['out']
                if out is not None:
                    replica = msg.model_copy(
                        update={'dst_mask': submask,
                                'dst': self._neighbor(direction)})
                    out.put(replica)
                    delivered = True
        if not delivered:
            raise UnboundLocalPortError(
                f"router {self.id} could not route multicast mask {mask:b}")

    def _multicast_branches(self, mask):
        branches = []
        rx, ry = self.to_xy(self.id)
        east = west = north = south = 0
        for rid in range(self.dim_x * self.dim_y):
            if not (mask & (1 << rid)):
                continue
            tx, ty = self.to_xy(rid)
            if tx > rx:
                east |= 1 << rid
            elif tx < rx:
                west |= 1 << rid
            elif ty > ry:
                north |= 1 << rid
            elif ty < ry:
                south |= 1 << rid
        if east:
            branches.append((Direction.EAST, east))
        if west:
            branches.append((Direction.WEST, west))
        if north:
            branches.append((Direction.NORTH, north))
        if south:
            branches.append((Direction.SOUTH, south))
        return branches

    def _neighbor(self, direction):
        x, y = self.to_xy(self.id)
        if direction == Direction.EAST:
            return self.to_id(x + 1, y)
        if direction == Direction.WEST:
            return self.to_id(x - 1, y)
        if direction == Direction.NORTH:
            return self.to_id(x, y + 1)
        return self.to_id(x, y - 1)

    def _handle_reduce(self, msg):
        task_id = msg.task_id
        if self.reduce_trees is None or task_id not in self.reduce_trees:
            raise ProfilingSimError(
                f"router {self.id} received reduce packet for "
                f"unregistered task_id {task_id}")
        tree = self.reduce_trees[task_id]
        if self.id not in tree:
            raise ProfilingSimError(
                f"router {self.id} not on reduction tree for task {task_id}")
        if task_id not in self.reduce_stores:
            self.reduce_stores[task_id] = simpy.Store(self.env)
        self.reduce_stores[task_id].put(msg)
        if task_id not in self.reduce_active:
            self.reduce_active.add(task_id)
            self.env.process(self._reduce_run(task_id))

    def _reduce_run(self, task_id):
        assert self.reduce_trees is not None
        node = self.reduce_trees[task_id][self.id]
        children = node['children']
        k = len(children)
        store = self.reduce_stores[task_id]
        operands = []
        for _ in range(k):
            operands.append((yield store.get()))
        if k >= 2:
            stages = 0
            n = k
            while n > 1:
                stages += 1
                n = (n + 1) // 2
            yield self.env.timeout(self.reduce_latency * stages)
        op = operands[0].reduce_op
        if op == 1:
            merged_value = sum(m.value for m in operands)
        elif op == 2:
            merged_value = max(m.value for m in operands)
        else:
            merged_value = operands[0].value
        is_last = node['is_root']
        ins_mode = 3 if is_last else 2
        merged = operands[0].model_copy(update={
            'reduce_count': sum(m.reduce_count for m in operands),
            'reduce_is_last': is_last,
            'ins_sync_mode': ins_mode,
            'value': merged_value,
        })
        if node['is_root']:
            yield self.env.process(
                self.route_local(merged, merged.dst_local_port))
        else:
            yield self.env.timeout(self.per_hop_time)
            yield self.env.process(self.route(merged, node['parent']))

    def run(self):
        while True:
            channels = []
            for idx, d in enumerate(Direction):
                ch = self.links[d]['in']
                if ch is not None:
                    channels.append((ch, d, False))
            for port in sorted(self.local_ports):
                ch = self.local_ports[port]['in']
                if ch is not None:
                    channels.append((ch, port, True))

            with contextlib.ExitStack() as stack:
                all_events = [stack.enter_context(ch[0].get()) for ch in channels]
                yield self.env.any_of(all_events)

                for id_val, event in enumerate(all_events):
                    if not event.triggered:
                        continue
                    ch, port, from_local = channels[id_val]
                    self._drain(ch, event.value, from_local, port)
                    while ch.len() > 0:
                        msg = yield ch.get()
                        self._drain(ch, msg, from_local, port)

    def _drain(self, ch, msg, from_local, in_port=None):
        self.env.process(self.routing(msg, from_local, in_port))

    def calculate_next_router(self, target_id):
        x, y = self.to_xy(self.id)
        tx, ty = self.to_xy(target_id)

        if x != tx:
            if tx > x:
                return Direction.EAST
            else:
                return Direction.WEST
        if y != ty:
            if ty > y:
                return Direction.NORTH
            else:
                return Direction.SOUTH
        return Direction.EAST

    def to_id(self, x, y):
        return y * self.dim_x + x

    def to_xy(self, rid):
        return rid % self.dim_x, rid // self.dim_x


class NoC:
    def __init__(self, env, config, deterministic: bool = False, rank: int = 0):
        self.env = env
        self.config = config
        self.x = config.x
        self.y = config.y
        self.rank = rank
        self.router_config = config.router
        self.link_config = config.link
        self.deterministic = deterministic
        self.r2r_links = []
        self.local_links = []
        self.routers = []
        self.node_router = {}
        self.egress = {}
        self.reduce_trees = {}
        self.fifos = {}

    def build(self):
        concentrated = set(getattr(self.config, 'concentrated_routers', []) or [])
        lic = self.router_config.local_injection_capacity
        for rid in range(self.x * self.y):
            cap = lic if (lic > 0 and rid in concentrated) else 0
            r = Router(self.env, self.router_config, rid, self.x, self.y,
                       local_inj_cap=cap)
            r.chip_rank = self.rank
            r.egress_table = self.egress
            r.node_router = self.node_router
            r.reduce_trees = self.reduce_trees
            r.fifos = self.fifos
            self.routers.append(r)

        for y in range(self.y):
            for x in range(self.x):
                rid = y * self.x + x
                if x < self.x - 1:
                    self._connect(rid, Direction.EAST, rid + 1, Direction.WEST)
                if y < self.y - 1:
                    self._connect(rid, Direction.NORTH,
                                  rid + self.x, Direction.SOUTH)

        return self

    def _connect(self, rid, dir_a, peer_id, dir_b):
        bb = self.router_config.burst_bubble
        l1 = Link(self.env, self.link_config, deterministic=self.deterministic,
                  burst_bubble=bb)
        l2 = Link(self.env, self.link_config, deterministic=self.deterministic,
                  burst_bubble=bb)
        l2.bind(rid, int(dir_a), peer_id, int(dir_b), True)
        l1.bind(peer_id, int(dir_b), rid, int(dir_a), True)
        self.routers[rid].bind_link(dir_a, l1, l2)
        self.routers[peer_id].bind_link(dir_b, l2, l1)
        self.r2r_links.extend([l1, l2])

    def attach_local(self, router_id: int, port: int, node_id: Optional[int] = None):
        if node_id is None:
            node_id = router_id
        if node_id >= 0:
            self.node_router[node_id] = router_id
        bb = self.router_config.burst_bubble
        node_to_router = Link(self.env, self.link_config,
                              deterministic=self.deterministic, burst_bubble=bb)
        router_to_node = Link(self.env, self.link_config,
                              deterministic=self.deterministic, burst_bubble=bb)
        node_to_router.bind(node_id, port, router_id, port, False)
        router_to_node.bind(router_id, port, node_id, port, False)
        self.routers[router_id].bind_local_port(
            port, node_to_router, router_to_node)
        self.local_links.extend([node_to_router, router_to_node])
        return router_to_node, node_to_router

    def register_reduce(self, task_id, source_routers, root_router, op=1):
        n = self.x * self.y
        if task_id in self.reduce_trees:
            raise ValueError(f"reduce task_id {task_id} already registered")
        if not source_routers:
            raise ValueError("reduce requires at least one source")
        if len(set(source_routers)) != len(source_routers):
            raise ValueError("duplicate source routers in reduce")
        for r in list(source_routers) + [root_router]:
            if not (0 <= r < n):
                raise ValueError(f"router id {r} out of range [0,{n})")
        tree = {}

        def node(rid):
            if rid not in tree:
                tree[rid] = {'children': set(), 'parent': None,
                             'is_root': rid == root_router}
            return tree[rid]

        for src in source_routers:
            path = self._xy_path(src, root_router)
            for idx, rid in enumerate(path):
                cur = node(rid)
                if idx == 0:
                    cur['children'].add("local")
                if idx + 1 < len(path):
                    nxt = path[idx + 1]
                    cur['parent'] = self.routers[rid].calculate_next_router(nxt)
                    incoming = self.routers[nxt].calculate_next_router(rid)
                    node(nxt)['children'].add(incoming)
        self.reduce_trees[task_id] = tree
        return tree

    def _xy_path(self, src, dst):
        path = [src]
        cur = src
        while cur != dst:
            d = self.routers[cur].calculate_next_router(dst)
            cur = self.routers[cur]._neighbor(d)
            path.append(cur)
        return path

    def register_fifo(self, hw_id: int, logic_id: int, depth: int):
        if not (0 <= hw_id <= 63):
            raise ValueError(f"fifo hw_id {hw_id} out of range [0,63]")
        if not (0 <= logic_id <= 31):
            raise ValueError(f"fifo logic_id {logic_id} out of range [0,31]")
        if depth < 1:
            raise ValueError(f"fifo depth {depth} must be >= 1")
        key = (hw_id, logic_id)
        if key in self.fifos:
            raise ValueError(f"fifo {key} already registered")
        self.fifos[key] = {
            'depth': depth,
            'credits': simpy.Container(self.env, init=depth, capacity=depth),
        }
        return self.fifos[key]
