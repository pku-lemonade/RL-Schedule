import simpy
import logging
import contextlib
from enum import IntEnum
from typing import Dict, List, Optional

from .utils.definitions import Slice, Direction, Message, ceil, Event
from .configs.schemas.arch_config import *
from .distribution import NoCDist


logger = logging.getLogger("NoC")


class Link:
    def __init__(self, env, config, capacity=1):
        # basic parameters
        self.env = env
        self.delay = config.delay
        self.bandwidth = config.width
        self.store = simpy.Store(env, capacity=capacity)
        self.delay_factor = 1  # fail-slow factor

        # performance variance model
        self.rate = 0.5
        self.shape = self.bandwidth * self.rate
        self.para_dist = NoCDist(shape=self.shape, rate=self.rate)

        # data collection
        self.events = []
        self.index2id = {}

        # other information
        self.hop_num = 0


    def len(self):
        return len(self.store.items)
    
    def bind(self, idx1, idx2, id1, id2, tag):
        self.corefrom = idx1
        self.coreto = idx2
        self.tag = tag
        self.corefromid = id1
        self.coretoid = id2

    def calc_latency(self, msg):
        slice = Slice(tensor_slice=msg.data)
        var_bandwidth = self.para_dist.generate()
        transmission_time = ceil(slice.size(), var_bandwidth)

        latency = self.delay + transmission_time
        latency = latency * self.delay_factor

        yield self.env.timeout(latency)
        yield self.store.put(msg)
    
    def put(self, msg):
        self.events.append(Event(index = msg.index,
                                 start_time = int(round(self.env.now)),
                                 src_id = self.corefromid,
                                 dst_id = self.coretoid,
                                 data_size = Slice(tensor_slice=msg.data).size()))
        self.index2id[msg.index] = len(self.events) - 1

        return self.env.process(self.calc_latency(msg))

    def get(self):
        return self.store.get()

    def change_delay(self, times):
        self.delay_factor *= times

    def recover_delay(self, times):
        self.delay_factor /= times


class Router:
    def __init__(self, env, config: RouterConfig, id: int,
                 x: int, y:int, per_hop_time=1, start_up_time=1):
        # basic parameters
        self.env = env
        self.virtual_channel = config.vc
        self.routing_algorithm = config.type
        self.id, self.x, self.y = id, x, y
        self.per_hop_time = per_hop_time
        self.start_up_time = start_up_time

        # 5-direction links
        self.links: Dict[object, Dict[str, Optional[Link]]] = {
            Direction.NORTH: {'in': None, 'out': None},
            Direction.SOUTH: {'in': None, 'out': None},
            Direction.EAST:  {'in': None, 'out': None},
            Direction.WEST:  {'in': None, 'out': None},
            'core':          {'in': None, 'out': None}
        }

        self.env.process(self.run())


    def bind_link(self, direction, link_in, link_out):
        """Bind input/output links for the given direction."""
        self.links[direction]['in'] = link_in
        self.links[direction]['out'] = link_out


    def router_fail(self, times):
        for dir in Direction:
            link_in = self.links[dir]['in']
            link_out = self.links[dir]['out']
            assert link_in is not None and link_out is not None
            link_in.change_delay(times)
            link_out.change_delay(times)


    def router_recover(self, times):
        for dir in Direction:
            link_in = self.links[dir]['in']
            link_out = self.links[dir]['out']
            assert link_in is not None and link_out is not None
            link_in.recover_delay(times)
            link_out.recover_delay(times)


    def route(self, msg: Message, next_dir, next_router):
        link = self.links[next_dir]['out']
        assert link is not None
        yield link.put(msg)

    def route_core(self, msg):
        link = self.links['core']['out']
        assert link is not None
        yield link.put(msg)

    def routing(self, msg):
        if msg.dst == self.id:
            logger.info(f"Time {self.env.now: .2f}: Router {self.id} is routing package to Core {self.id}")
            yield self.env.process(self.route_core(msg))
        else:
            yield self.env.timeout(self.per_hop_time)
            next_dir, next_router = self.calculate_next_router(msg.dst)
            logger.info(f"Time {self.env.now: .2f}: Router {self.id} is routing package to Router {next_router}")
            yield self.env.process(self.route(msg, next_dir, next_router))


    def run(self):
        while True:
            # calculate existing channels
            all_possible_channels: List[tuple] = [
                (self.links[dir]['in'], idx) for idx, dir in enumerate(Direction)
            ]
            all_possible_channels.append((self.links['core']['in'], 4))
            all_channels = [(link, idx) for link, idx in all_possible_channels if link is not None]

            with contextlib.ExitStack() as stack:
                all_events = [stack.enter_context(link.get()) for link, _ in all_channels]
                events = self.env.any_of(all_events)
                result = yield events

                for id, event in enumerate(all_events):
                    if event.triggered:
                        msg = event.value

                        logger.info(f"Time {self.env.now: .2f}: Router {self.id} received a package aim to Core {msg.dst}")
                        self.env.process(self.routing(msg))

                        # 需要全取出来吗？
                        channel = None
                        match all_channels[id][1]:
                            case 0: channel = self.links[Direction.NORTH]['in']
                            case 1: channel = self.links[Direction.SOUTH]['in']
                            case 2: channel = self.links[Direction.EAST]['in']
                            case 3: channel = self.links[Direction.WEST]['in']
                            case 4: channel = self.links['core']['in']

                        assert channel is not None
                        channel.events[channel.index2id[msg.index]].end_time = int(round(self.env.now))

                        while channel.len() > 0:
                            msg = yield channel.get()
                            channel.events[channel.index2id[msg.index]].end_time = int(round(self.env.now))

                            self.env.process(self.routing(msg))

    
    def get_layer(self, x: int, y: int):
        return min(x, self.x - 1 - x, y, self.y - 1 - y)
    

    def is_corner(self, x: int, y: int):
        return min(x, self.x - 1 - x) == min(y, self.y - 1 - y)
    

    def outer(self, x: int, y: int, layer_id: int):
        new_x = x - 1 if x == layer_id else x + 1
        new_y = y - 1 if y == layer_id else y + 1
        return self.to_id(new_x, new_y)


    def inner(self, x: int, y: int, layer_id: int):
        new_x = x + 1 if x == layer_id else x - 1
        new_y = y + 1 if y == layer_id else y - 1
        return self.to_id(new_x, new_y)


    def ring_next(self, x: int, y: int, layer_id: int):
        high_x = self.x - 1 - layer_id
        high_y = self.y - 1 - layer_id
        if x == layer_id and y == layer_id:
            return self.to_id(x + 1, y)
        if x == high_x and y == layer_id:
            return self.to_id(x, y + 1)
        if x == high_x and y == high_y:
            return self.to_id(x - 1, y)
        if x == layer_id and y == high_y:
            return self.to_id(x, y - 1)
        if y == layer_id:
            return self.to_id(x + 1, y)
        if x == high_x:
            return self.to_id(x, y + 1)
        if y == high_y:
            return self.to_id(x - 1, y)
        if x == layer_id:
            return self.to_id(x, y - 1)
        return self.to_id(x, y)
            
    # mapping mesh-style id to dragonfly id
    def get_dragonfly_info(self, router_id):
        """
        Group 0 (BL): [0, 1, 4, 5]   (x<2, y<2)
        Group 1 (TL): [8, 9, 12, 13] (x<2, y>=2)
        Group 2 (BR): [2, 3, 6, 7]   (x>=2, y<2)
        Group 3 (TR): [10, 11, 14, 15] (x>=2, y>=2)
        Position k within group is the gateway to/from group k.
        """
        groups = [
            [0, 1, 4, 5],
            [8, 9, 12, 13],
            [2, 3, 6, 7],
            [10, 11, 14, 15],
        ]
        
        gid = -1
        l_idx = -1
        
        for g_idx, nodes in enumerate(groups):
            if router_id in nodes:
                gid = g_idx
                l_idx = nodes.index(router_id)
                return gid, l_idx, nodes
        
        raise ValueError(f"Router ID {router_id} not valid for 4x4 Dragonfly mapping")
        

    def calculate_next_router(self, target_id) -> tuple:
        if self.routing_algorithm == "XY":
            now_x, now_y = self.to_xy(self.id)
            tar_x, tar_y = self.to_xy(target_id)

            if now_x != tar_x:
                if tar_x > now_x:
                    return Direction.EAST, self.to_id(now_x + 1, now_y)
                else:
                    return Direction.WEST, self.to_id(now_x - 1, now_y)
        
            if now_y != tar_y:
                if tar_y > now_y:
                    return Direction.NORTH, self.to_id(now_x, now_y + 1)
                else:
                    return Direction.SOUTH, self.to_id(now_x, now_y - 1)
                
        elif self.routing_algorithm == "Torus_XY":
            now_x, now_y = self.to_xy(self.id)
            tar_x, tar_y = self.to_xy(target_id)

            if now_x != tar_x:
                delta_x = tar_x - now_x
                if abs(delta_x) > self.x / 2:
                    if delta_x > 0:
                        return Direction.WEST, self.to_id(now_x - 1, now_y)
                    else:
                        return Direction.EAST, self.to_id(now_x + 1, now_y)
                else:
                    if delta_x > 0:
                        return Direction.EAST, self.to_id(now_x + 1, now_y)
                    else:
                        return Direction.WEST, self.to_id(now_x - 1, now_y)
                    
            if now_y != tar_y:
                delta_y = tar_y - now_y
                if abs(delta_y) > self.y / 2:
                    if delta_y > 0:
                        return Direction.SOUTH, self.to_id(now_x, now_y - 1)
                    else:
                        return Direction.NORTH, self.to_id(now_x, now_y + 1)
                else:
                    if delta_y > 0:
                        return Direction.NORTH, self.to_id(now_x, now_y + 1)
                    else:
                        return Direction.SOUTH, self.to_id(now_x, now_y - 1)
                    
        elif self.routing_algorithm == "RingRoad":
            now_x, now_y = self.to_xy(self.id)
            tar_x, tar_y = self.to_xy(target_id)

            now_layer = self.get_layer(now_x, now_y)
            tar_layer = self.get_layer(tar_x, tar_y)

            # route to the same layer first
            if now_layer != tar_layer:
                if self.is_corner(now_x, now_y):
                    if now_layer > tar_layer:
                        return Direction.NORTH, self.outer(now_x, now_y, now_layer)
                    else:
                        return Direction.SOUTH, self.inner(now_x, now_y, now_layer)
                else:
                    return Direction.EAST, self.ring_next(now_x, now_y, now_layer)
            else:
                return Direction.EAST, self.ring_next(now_x, now_y, now_layer)

        elif self.routing_algorithm == "Dragonfly":
            my_gid, my_lidx, my_group_nodes = self.get_dragonfly_info(self.id)
            tgt_gid, tgt_lidx, tgt_group_nodes = self.get_dragonfly_info(target_id)
            
            # Case 1: intra-group routing
            if my_gid == tgt_gid:
                next_hop_id = target_id
                
            # Case 2: inter-group routing
            else:
                gateway_lidx = tgt_gid
                
                if my_lidx == gateway_lidx:
                    next_hop_id = tgt_group_nodes[my_gid] 
                    
                    next_x, next_y = self.to_xy(next_hop_id)
                    return Direction.WEST, self.to_id(next_x, next_y)
                
                else:
                    next_hop_id = my_group_nodes[gateway_lidx]

            neighbors = sorted([n for n in my_group_nodes if n != self.id])
            neighbor_pos = neighbors.index(next_hop_id)
            
            intra_ports = [Direction.NORTH, Direction.SOUTH, Direction.EAST]
            next_dir = intra_ports[neighbor_pos]
            
            next_x, next_y = self.to_xy(next_hop_id)
            return next_dir, self.to_id(next_x, next_y)

        raise ValueError(f"Cannot route to {target_id} using {self.routing_algorithm}")


    def to_id(self, x, y):
        return y * self.x + x

    def to_xy(self, id):
        x = id % self.x
        y = id // self.x
        return x, y


class NoC:
    def __init__(self, env, config: NoCConfig):
        # basic parameters
        self.env = env
        self.x = config.x
        self.y = config.y
        self.router_config = config.router
        self.link_config = config.link

        # components
        self.r2r_links = []
        self.routers = []

    def build_connection_mesh(self):
        for id in range(self.x * self.y):
            self.routers.append(Router(self.env, self.router_config, id, self.x, self.y))

        for y in range(self.y):
            for x in range(self.x):
                router_id = y * self.x + x
                if x < self.x - 1:
                    east_id = y * self.x + (x + 1)
                    link1 = Link(self.env, self.link_config)
                    link2 = Link(self.env, self.link_config)

                    link2.bind((x, y), (x+1, y), router_id, east_id, True)
                    link1.bind((x+1, y), (x, y), east_id, router_id, True)

                    self.routers[router_id].bind_link(Direction.EAST, link1, link2)
                    self.routers[east_id].bind_link(Direction.WEST, link2, link1)

                    self.r2r_links.append(link1)
                    self.r2r_links.append(link2)

                if y < self.y - 1:
                    north_id = (y + 1) * self.x + x

                    link1 = Link(self.env, self.link_config)
                    link2 = Link(self.env, self.link_config)

                    link2.bind((x, y), (x, y+1), router_id, north_id, True)
                    link1.bind((x, y+1), (x, y), north_id, router_id, True)

                    self.routers[router_id].bind_link(Direction.NORTH, link1, link2)
                    self.routers[north_id].bind_link(Direction.SOUTH, link2, link1)

                    self.r2r_links.append(link1)
                    self.r2r_links.append(link2)

        return self
    

    def build_connection_torus(self):
        for id in range(self.x * self.y):
            self.routers.append(Router(self.env, self.router_config, id, self.x, self.y))

        for y in range(self.y):
            for x in range(self.x):
                router_id = y * self.x + x

                east_x = (x + 1) % self.x
                east_id = y * self.x + east_x

                link1 = Link(self.env, self.link_config)
                link2 = Link(self.env, self.link_config)

                link2.bind((x, y), (east_x, y), router_id, east_id, True)
                link1.bind((east_x, y), (x, y), east_id, router_id, True)

                self.routers[router_id].bind_link(Direction.EAST, link1, link2)
                self.routers[east_id].bind_link(Direction.WEST, link2, link1)

                self.r2r_links.append(link1)
                self.r2r_links.append(link2)

                north_y = (y + 1) % self.y
                north_id = north_y * self.x + x

                link1 = Link(self.env, self.link_config)
                link2 = Link(self.env, self.link_config)

                link2.bind((x, y), (x, north_y), router_id, north_id, True)
                link1.bind((x, north_y), (x, y), north_id, router_id, True)

                self.routers[router_id].bind_link(Direction.NORTH, link1, link2)
                self.routers[north_id].bind_link(Direction.SOUTH, link2, link1)

                self.r2r_links.append(link1)
                self.r2r_links.append(link2)

        return self
    
    def get_layer(self, x, y):
        return min(x, self.x - 1 - x, y, self.y - 1 - y)


    def get_ring_nodes_ordered(self, k: int):
        nodes = []
        low_x, low_y = k, k
        high_x, high_y = self.x - 1 - k, self.y - 1 - k

        if low_x == high_x and low_y == high_y:
            return [(low_x, low_y)]

        for x in range(low_x, high_x):
            nodes.append((x, low_y))

        for y in range(low_y, high_y):
            nodes.append((high_x, y))

        for x in range(high_x, low_x, -1):
            nodes.append((x, high_y))

        for y in range(high_y, low_y, -1):
            nodes.append((low_x, y))

        return nodes


    def build_connection_ring_road(self):
        for id in range(self.x * self.y):
            self.routers.append(Router(self.env, self.router_config, id, self.x, self.y))

        num_layers = (min(self.x, self.y) + 1) // 2

        for k in range(num_layers):
            ring_nodes = self.get_ring_nodes_ordered(k)
            num_nodes = len(ring_nodes)

            if num_nodes > 1:
                for i in range(num_nodes):
                    curr_coords = ring_nodes[i]
                    next_coords = ring_nodes[(i + 1) % num_nodes]

                    curr_id = curr_coords[1] * self.x + curr_coords[0]
                    next_id = next_coords[1] * self.x + next_coords[0]

                    link_cw = Link(self.env, self.link_config)
                    link_ccw = Link(self.env, self.link_config)

                    link_cw.bind(curr_id, next_id, curr_id, next_id, True)
                    link_ccw.bind(next_id, curr_id, next_id, curr_id, True)

                    self.r2r_links.append(link_cw)
                    self.r2r_links.append(link_ccw)

                    self.routers[curr_id].bind_link(Direction.EAST, link_ccw, link_cw)
                    self.routers[next_id].bind_link(Direction.WEST, link_cw, link_ccw)

            if k < num_layers - 1:
                low = k
                high_x = self.x - 1 - k
                high_y = self.y - 1 - k

                next_low = k + 1
                next_high_x = self.x - 1 - (k + 1)
                next_high_y = self.y - 1 - (k + 1)

                corners = [
                    ((low, low),         (next_low, next_low)),
                    ((high_x, low),      (next_high_x, next_low)),
                    ((high_x, high_y),   (next_high_x, next_high_y)),
                    ((low, high_y),      (next_low, next_high_y)),
                ]

                for (outer_pos, inner_pos) in corners:
                    outer_id = outer_pos[1] * self.x + outer_pos[0]
                    inner_id = inner_pos[1] * self.x + inner_pos[0]

                    link_inward = Link(self.env, self.link_config)
                    link_outward = Link(self.env, self.link_config)

                    link_inward.bind(outer_id, inner_id, outer_id, inner_id, True)
                    link_outward.bind(inner_id, outer_id, inner_id, outer_id, True)

                    self.r2r_links.append(link_inward)
                    self.r2r_links.append(link_outward)

                    self.routers[outer_id].bind_link(Direction.SOUTH, link_outward, link_inward)
                    self.routers[inner_id].bind_link(Direction.NORTH, link_inward, link_outward)

        return self


    def build_connection_dragonfly(self):
        for id in range(self.x * self.y):
            self.routers.append(Router(self.env, self.router_config, id, self.x, self.y))

        all_groups = [
            [0, 1, 4, 5],      # Group 0 (BL: x<2, y<2)
            [8, 9, 12, 13],    # Group 1 (TL: x<2, y>=2)
            [2, 3, 6, 7],      # Group 2 (BR: x>=2, y<2)
            [10, 11, 14, 15]   # Group 3 (TR: x>=2, y>=2)
        ]

        local_ports = [Direction.NORTH, Direction.SOUTH, Direction.EAST]
        global_port = Direction.WEST

        for g_idx, nodes in enumerate(all_groups):
            group_size = len(nodes)

            for i in range(group_size):
                for j in range(i + 1, group_size):
                    u_id = nodes[i]
                    v_id = nodes[j]

                    u_neighbors = [n for n in nodes if n != u_id]
                    u_port_idx = u_neighbors.index(v_id)
                    dir_u = local_ports[u_port_idx]

                    v_neighbors = [n for n in nodes if n != v_id]
                    v_port_idx = v_neighbors.index(u_id)
                    dir_v = local_ports[v_port_idx]

                    link1 = Link(self.env, self.link_config)
                    link2 = Link(self.env, self.link_config)

                    cu = self.routers[u_id].to_xy(u_id)
                    cv = self.routers[v_id].to_xy(v_id)

                    link1.bind(cu, cv, u_id, v_id, True)
                    link2.bind(cv, cu, v_id, u_id, True)

                    self.routers[u_id].bind_link(dir_u, link2, link1)
                    self.routers[v_id].bind_link(dir_v, link1, link2)

                    self.r2r_links.append(link1)
                    self.r2r_links.append(link2)

        num_groups = len(all_groups)
        for g_src in range(num_groups):
            for g_dst in range(g_src + 1, num_groups):

                src_group_nodes = all_groups[g_src]
                src_id = src_group_nodes[g_dst]

                dst_group_nodes = all_groups[g_dst]
                dst_id = dst_group_nodes[g_src]

                link_out = Link(self.env, self.link_config)
                link_in = Link(self.env, self.link_config)

                c_src = self.routers[src_id].to_xy(src_id)
                c_dst = self.routers[dst_id].to_xy(dst_id)

                link_out.bind(c_src, c_dst, src_id, dst_id, True)
                link_in.bind(c_dst, c_src, dst_id, src_id, True)

                self.routers[src_id].bind_link(global_port, link_in, link_out)
                self.routers[dst_id].bind_link(global_port, link_out, link_in)

                self.r2r_links.append(link_in)
                self.r2r_links.append(link_out)

        return self
