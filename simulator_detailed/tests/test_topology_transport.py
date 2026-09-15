"""Independent route expectations and admission/binding resource invariants."""

import unittest

import simpy

from simulator_detailed.configs.schemas.arch_config import (
    LinkConfig,
    NoCConfig,
    RouterConfig,
)
from simulator_detailed.configs.schemas.topology import CanonicalTopology, ExplicitRoute
from simulator_detailed.noc import Link, NoCTracer, Router
from simulator_detailed.routing import ExplicitRouting, LegacyXYRouting, channel_id
from simulator_detailed.tests.test_topology import reference_graph
from simulator_detailed.topology import Topology, topology_from_legacy
from simulator_detailed.utils.definitions import (
    DIR_EAST,
    DIR_NORTH,
    DIR_SOUTH,
    DIR_WEST,
    Flit,
    FlitConfig,
    FlitType,
    NoCChannel,
)


def explicit_policy(document=None, routes=None):
    topology = Topology.compile(CanonicalTopology.model_validate(document or reference_graph()))
    routes = routes if routes is not None else [{"fabric_id": 0, "source": "source", "destination": "sink",
                                                "link_ids": ["a-ram", "ram-off", "off-hop", "hop-z"]}]
    return ExplicitRouting.compile(topology, tuple(ExplicitRoute.model_validate(r) for r in routes),
                                   {0: FlitConfig()}, "a" * 64)


def graph_flit(policy, **updates):
    source, sink = policy.endpoint("source"), policy.endpoint("sink")
    return Flit(flit_type=FlitType.SINGLE, payload_bytes=5, msg_id=1,
                fabric_id=NoCChannel.CH0, src_router=source.router_id,
                src_local_port=source.inject_port, dst_router=sink.router_id,
                dst_local_port=sink.eject_port, mesh_x=None, mesh_y=None,
                transport_id=policy.plan_id).model_copy(update=updates)


class DirectedTransportTests(unittest.TestCase):
    def test_all_pairs_xy_matches_independent_coordinate_oracle(self):
        for width, height in ((1, 1), (3, 2), (4, 4)):
            topology = topology_from_legacy(NoCConfig(x=width, y=height))
            policy = LegacyXYRouting(topology, 0)
            for source in range(width * height):
                for destination in range(width * height):
                    flit = Flit(flit_type=FlitType.SINGLE, payload_bytes=1, msg_id=1,
                                fabric_id=NoCChannel.CH0, src_router=source, dst_router=destination,
                                mesh_x=width, mesh_y=height, dst_local_port=9)
                    x, y = source % width, source // width
                    dx, dy = destination % width, destination // width
                    while (x, y) != (dx, dy):
                        expected = (DIR_EAST if x < dx else DIR_WEST) if x != dx else (
                            DIR_NORTH if y < dy else DIR_SOUTH)
                        self.assertEqual(policy.next_port(y * width + x, flit), expected)
                        x += (x < dx) - (x > dx)
                        if x == dx and expected in (DIR_NORTH, DIR_SOUTH):
                            y += (y < dy) - (y > dy)
                    self.assertEqual(policy.next_port(destination, flit), 9)
            if width > 1:
                policy.outputs.pop((0, "EAST"))
                with self.assertRaisesRegex(ValueError, "unavailable XY"):
                    policy.next_port(0, flit.model_copy(update={"dst_router": 1}))

    def test_explicit_path_and_same_router_route(self):
        policy = explicit_policy()
        flit = graph_flit(policy)
        path = policy.route_for(flit)
        self.assertEqual([r for r, _, _ in path.hops], [0, 3, 2, 1, 4])
        self.assertEqual(len(path.channels), 6)
        for router, incoming, outgoing in path.hops:
            policy.validate_router(flit, router, incoming)
            self.assertEqual(policy.next_port(router, flit), outgoing)
        local = explicit_policy(routes=[{"fabric_id": 0, "source": "source", "destination": "source", "link_ids": []}])
        self.assertEqual(len(next(iter(local.paths.values())).hops), 1)
        self.assertIsNone(flit.mesh_x)

    def test_route_admission_failures(self):
        cases = [
            lambda d, r: r[0].update(link_ids=["ram-off"]),
            lambda d, r: r[0].update(source="sink", destination="source"),
            lambda d, r: r[0].update(link_ids=[]),
            lambda d, r: d["links"][0].update(enabled=False),
            lambda d, r: d["routers"][3].update(enabled=False),
            lambda d, r: d["fabrics"][0].update(topology_policy="torus"),
            lambda d, r: d.update(connectivity_state="unresolved"),
            lambda d, r: r[0].update(source="ram-noc0"),
            lambda d, r: d["attachments"][0].update(inject_port=None),
            lambda d, r: r.append(r[0]),
        ]
        for change in cases:
            document = reference_graph()
            routes = [{"fabric_id": 0, "source": "source", "destination": "sink",
                       "link_ids": ["a-ram", "ram-off", "off-hop", "hop-z"]}]
            change(document, routes)
            with self.subTest(change=change), self.assertRaises((ValueError, NotImplementedError)):
                explicit_policy(document, routes)

    def test_union_cycle_of_individually_simple_routes(self):
        document = reference_graph()
        # Close a physical ring and add a terminal at its middle. A safe subset
        # remains executable, but these three simple routes form a dependency cycle.
        for router in document["routers"][:5]:
            if router["router_id"] == "z":
                router["ports"].append({"port_id": "next", "kind": "network"})
            if router["router_id"] == "a":
                router["ports"].append({"port_id": "prev", "kind": "network"})
            if router["router_id"] == "off":
                router["ports"].append({"port_id": "terminal", "kind": "local"})
        document["links"].append({"link_id": "z-a", "fabric_id": 0, "src_router": "z", "src_port": "next",
                                  "dst_router": "a", "dst_port": "prev", "enabled": True})
        document["attachments"].append({"endpoint_id": "middle", "fabric_id": 0, "router_id": "off",
                                        "role": "network", "enabled": True, "permissions_resolved": True,
                                        "replay_enabled": True, "inject_port": "terminal", "eject_port": "terminal"})
        routes = [
            {"fabric_id": 0, "source": "source", "destination": "sink",
             "link_ids": ["a-ram", "ram-off", "off-hop", "hop-z"]},
            {"fabric_id": 0, "source": "middle", "destination": "source",
             "link_ids": ["off-hop", "hop-z", "z-a"]},
            {"fabric_id": 0, "source": "sink", "destination": "middle",
             "link_ids": ["z-a", "a-ram", "ram-off"]},
        ]
        for route in routes:
            explicit_policy(document, [route])
        with self.assertRaisesRegex(ValueError, "channel dependency cycle"):
            explicit_policy(document, routes)
        with self.assertRaisesRegex(ValueError, "repeats"):
            explicit_policy(document, [{**routes[0], "destination": "source", "link_ids": routes[0]["link_ids"] + ["z-a"]}])

    def test_rejected_context_changes_no_transport_resources(self):
        policy = explicit_policy()
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        channel = channel_id("inject", 0, "source")
        link = Link(env, LinkConfig(), NoCChannel.CH0, tracer, noc_cycles_per_aci_cycle=1,
                    transport_context=policy, graph_channel_id=channel)
        router = Router(env, RouterConfig(), 0, None, None, NoCChannel.CH0, tracer,
                        routing=policy, transport_context=policy)
        cases = [
            {"transport_id": "b" * 64}, {"fabric_id": NoCChannel.CH1},
            {"format": FlitConfig(physical_flit_bytes=8, payload_capacity_bytes=8)},
            {"dst_local_port": 22}, {"src_router": 3}, {"is_broadcast": True},
        ]
        for updates in cases:
            flit = graph_flit(policy, **updates)
            with self.subTest(updates=updates), self.assertRaises((ValueError, NotImplementedError)):
                link.send_flit(flit)
            with self.assertRaises((ValueError, NotImplementedError)):
                router._rc_compute(0, flit)
            self.assertFalse(router.reservation)
            self.assertFalse(router._switch_grants)
            self.assertEqual(link.in_flight_flits, 0)
            self.assertFalse(link._out_queue.items)
        with self.assertRaisesRegex(ValueError, "outside"):
            policy.validate_channel(graph_flit(policy), channel_id("network", 7, "a-ram"))
        with self.assertRaisesRegex(ValueError, "router/input"):
            policy.validate_router(graph_flit(policy), 0, -99)

    def test_independent_bindings_and_atomic_pair_validation(self):
        env = simpy.Environment()
        tracer = NoCTracer(NoCChannel.CH0)
        router = Router(env, RouterConfig(), 0, 3, 2, NoCChannel.CH0, tracer)
        def link(name, other_env=None, other_tracer=None):
            return Link(other_env or env, LinkConfig(), NoCChannel.CH0, other_tracer or tracer,
                        name, noc_cycles_per_aci_cycle=1)
        incoming, outgoing = link("in"), link("out")
        router.bind_input(-10, incoming)
        self.assertNotIn(-10, router.port_out)
        router.bind_output(-11, outgoing)
        self.assertNotIn(-11, router.port_in)
        for invalid in (link("wrong-env", simpy.Environment()), link("wrong-tracer", other_tracer=NoCTracer(NoCChannel.CH0))):
            with self.assertRaises(ValueError):
                router.bind_link(9, link("candidate"), invalid)
            self.assertNotIn(9, router.port_in)
            self.assertNotIn(9, router.output_arbiters)
        with self.assertRaisesRegex(ValueError, "already bound"):
            router.bind_input(-12, incoming)
        router.scale_link_delay(2)
        self.assertEqual(incoming.delay_factor, 2)
        self.assertEqual(outgoing.delay_factor, 2)
