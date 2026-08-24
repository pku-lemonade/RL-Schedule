import unittest

import simpy

from simulator_detailed.configs.schemas.arch_config import LinkConfig, NoCConfig
from simulator_detailed.noc import FlitAction, Link, NoC, NoCTracer
from simulator_detailed.utils.definitions import PORT_PE, Flit, FlitType


class MeshHarness:
    def __init__(self):
        self.env = simpy.Environment()
        self.config = NoCConfig()
        self.tracer = NoCTracer()
        self.noc = NoC(self.env, self.config, self.tracer).build_connection_mesh()
        self.endpoints = {}

    def attach(self, router_id):
        c2r = Link(
            self.env,
            self.config.c2r_link,
            self.config.router.flit.flit_size,
            self.tracer,
            f"PE{router_id}->R{router_id}",
        )
        r2c = Link(
            self.env,
            self.config.c2r_link,
            self.config.router.flit.flit_size,
            self.tracer,
            f"R{router_id}->PE{router_id}",
        )
        self.noc.routers[router_id].bind_link(PORT_PE, c2r, r2c)
        self.endpoints[router_id] = (c2r, r2c)

    @staticmethod
    def flit(flit_type, msg_id, src, dst, payload=512):
        return Flit(
            flit_type=flit_type,
            payload_bytes=payload,
            msg_id=msg_id,
            src_router=src,
            dst_router=dst,
        )

    def transfer(self, src, dst, flits, ack_delays=None):
        for router_id in (src, dst):
            if router_id not in self.endpoints:
                self.attach(router_id)
        c2r = self.endpoints[src][0]
        r2c = self.endpoints[dst][1]
        arrivals = []
        ack_delays = ack_delays or [0.0] * len(flits)

        def sender():
            for flit in flits:
                yield c2r.send_flit(flit)

        def receiver():
            for delay in ack_delays:
                flit = yield r2c.recv_flit()
                arrivals.append((flit, self.env.now))
                if delay:
                    yield self.env.timeout(delay)
                r2c.ack_credit()

        self.env.process(sender())
        receive_process = self.env.process(receiver())
        self.env.run(until=receive_process)
        return arrivals


class Phase2NoCTests(unittest.TestCase):
    def test_config_and_mesh_shape(self):
        harness = MeshHarness()
        self.assertEqual((harness.noc.x, harness.noc.y), (4, 8))
        self.assertEqual(len(harness.noc.routers), 32)
        self.assertEqual(len(harness.noc.r2r_links), 104)
        self.assertEqual(harness.config.router.vc, 1)
        self.assertEqual(harness.config.link.phit_width, 128)
        self.assertEqual(harness.config.link.buffer_depth, 1)

    def test_single_flit_properties(self):
        single = MeshHarness.flit(FlitType.SINGLE, 1, 0, 1)
        head = MeshHarness.flit(FlitType.HEAD, 2, 0, 1)
        body = MeshHarness.flit(FlitType.BODY, 2, 0, 1)
        tail = MeshHarness.flit(FlitType.TAIL, 2, 0, 1)
        self.assertTrue(single.is_head)
        self.assertTrue(single.is_tail)
        self.assertTrue(head.is_head)
        self.assertFalse(head.is_tail)
        self.assertFalse(body.is_head)
        self.assertFalse(body.is_tail)
        self.assertFalse(tail.is_head)
        self.assertTrue(tail.is_tail)

    def test_direct_link_latency_and_steady_gap(self):
        env = simpy.Environment()
        tracer = NoCTracer()
        link = Link(env, LinkConfig(), 512, tracer, "probe")
        flits = [
            self._standalone_flit(FlitType.HEAD, 10),
            self._standalone_flit(FlitType.BODY, 10),
            self._standalone_flit(FlitType.TAIL, 10),
        ]
        arrivals = []

        def sender():
            for flit in flits:
                yield link.send_flit(flit)

        def receiver():
            for _ in flits:
                yield link.recv_flit()
                arrivals.append(env.now)
                link.ack_credit()

        env.process(sender())
        done = env.process(receiver())
        env.run(until=done)
        self.assertAlmostEqual(arrivals[0], 4.5)
        self.assertAlmostEqual(arrivals[1] - arrivals[0], 4.8)
        self.assertAlmostEqual(arrivals[2] - arrivals[1], 4.8)

    def test_single_flit_latency_for_one_to_ten_hops(self):
        for hops in range(1, 11):
            x = min(3, hops)
            y = hops - x
            dst = y * 4 + x
            harness = MeshHarness()
            flit = harness.flit(FlitType.SINGLE, hops, 0, dst)
            arrivals = harness.transfer(0, dst, [flit])
            self.assertAlmostEqual(
                arrivals[0][1],
                8.5 * hops + 13.0,
                msg=f"unexpected latency for {hops} hops",
            )
            self.assertFalse(
                any(e.action == FlitAction.STALL_SA for e in harness.tracer.events)
            )

    def test_x_y_symmetry(self):
        times = []
        for dst in (1, 4):
            harness = MeshHarness()
            flit = harness.flit(FlitType.SINGLE, dst, 0, dst)
            times.append(harness.transfer(0, dst, [flit])[0][1])
        self.assertEqual(times, [21.5, 21.5])

    def test_three_flit_wormhole_pipeline(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.HEAD, 20, 0, 3, 500),
            harness.flit(FlitType.BODY, 20, 0, 3, 508),
            harness.flit(FlitType.TAIL, 20, 0, 3, 492),
        ]
        arrivals = harness.transfer(0, 3, flits)
        times = [time for _, time in arrivals]
        for actual, expected in zip(times, (38.5, 43.3, 48.1)):
            self.assertAlmostEqual(actual, expected)
        self.assertFalse(harness.noc.routers[0].reservation)
        self.assertFalse(harness.noc.routers[3].reservation)

    def test_receiver_backpressure_logs_credit_stall(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.HEAD, 30, 0, 1),
            harness.flit(FlitType.BODY, 30, 0, 1),
            harness.flit(FlitType.TAIL, 30, 0, 1),
        ]
        arrivals = harness.transfer(0, 1, flits, ack_delays=[50, 0, 0])
        self.assertEqual(len(arrivals), 3)
        self.assertGreater(arrivals[1][1] - arrivals[0][1], 50)
        self.assertTrue(
            any(e.action == FlitAction.STALL_CREDIT for e in harness.tracer.events)
        )

    def test_output_contention_logs_sa_stall(self):
        harness = MeshHarness()
        for router_id in (0, 4, 3):
            harness.attach(router_id)
        flow_a = [
            harness.flit(FlitType.HEAD, 40, 0, 3),
            harness.flit(FlitType.BODY, 40, 0, 3),
            harness.flit(FlitType.TAIL, 40, 0, 3),
        ]
        flow_b = [harness.flit(FlitType.SINGLE, 41, 4, 3)]
        received = []

        def sender(src, flits):
            for flit in flits:
                yield harness.endpoints[src][0].send_flit(flit)

        def receiver():
            r2c = harness.endpoints[3][1]
            for _ in range(4):
                flit = yield r2c.recv_flit()
                received.append(flit.msg_id)
                r2c.ack_credit()

        harness.env.process(sender(0, flow_a))
        harness.env.process(sender(4, flow_b))
        done = harness.env.process(receiver())
        harness.env.run(until=done)
        self.assertCountEqual(received, [40, 40, 40, 41])
        self.assertTrue(
            any(e.action == FlitAction.STALL_SA for e in harness.tracer.events)
        )

    def test_fail_slow_scales_incident_links(self):
        harness = MeshHarness()
        harness.attach(0)
        harness.attach(1)
        harness.noc.routers[0].scale_link_delay(2.0)
        flit = harness.flit(FlitType.SINGLE, 50, 0, 1)
        arrivals = harness.transfer(0, 1, [flit])
        self.assertAlmostEqual(arrivals[0][1], 30.5)

    def test_single_packets_release_switch_allocation(self):
        harness = MeshHarness()
        flits = [
            harness.flit(FlitType.SINGLE, 60, 0, 1),
            harness.flit(FlitType.SINGLE, 61, 0, 1),
        ]
        arrivals = harness.transfer(0, 1, flits)
        self.assertEqual([flit.msg_id for flit, _ in arrivals], [60, 61])
        for router in (harness.noc.routers[0], harness.noc.routers[1]):
            self.assertFalse(router.reservation)
            self.assertFalse(router._sa_reqs)

    @staticmethod
    def _standalone_flit(flit_type, msg_id):
        return Flit(
            flit_type=flit_type,
            payload_bytes=512,
            msg_id=msg_id,
            src_router=0,
            dst_router=1,
        )


if __name__ == "__main__":
    unittest.main()
