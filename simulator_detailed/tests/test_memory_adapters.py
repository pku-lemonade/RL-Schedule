"""Legacy equivalence and exclusive capacity tests; no tensor substitutes."""

import importlib.util
import json
import pickle
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import simpy

from simulator_detailed.configs.schemas.arch_config import ArchConfig, SPMConfig
from simulator_detailed.configs.schemas.memory_replay import MemoryBuffer, MemoryReplay
from simulator_detailed.core import ScratchpadMemory
from simulator_detailed.embedding.hw_encoder import build_hardware_graph
from simulator_detailed.memory_adapters import (
    LegacyDMAAdapter,
    LegacyDMALifecycle,
    LegacyDMAResult,
    ScratchpadCapacityAdapter,
    ScratchpadCapacityState,
)
from simulator_detailed.memory_resources import BufferHandle
from simulator_detailed.memory_runtime import MemoryRuntime
from simulator_detailed.predictor.predict import detect
from simulator_detailed.tests import test_phase3_dma as dma_tests
from simulator_detailed.tests.test_memory_runtime import documents, execution_plan
from simulator_detailed.topology_compatibility import (
    legacy_event_rows,
    require_legacy_nocs,
    require_legacy_topology,
)
from simulator_detailed.tracing import collect_dma_service_events
from simulator_detailed.utils.definitions import (
    DimSlice,
    DMAAttachmentMode,
    DMACommandMode,
    Message,
    NMCShapeMode,
    NoCChannel,
    OperatorType,
    TransType,
)
from simulator_detailed.utils.dfg import DFGNode
from simulator_detailed.utils.task import Task


def command(arch, cores, node_type, read, mode, fabric, *, index=1, size=22):
    endpoint = arch.dma_endpoints[node_type, 17]
    pe = cores[5].nmc_channel_for(fabric)
    address = endpoint.binding_for(fabric).address
    message = Message(src=address if read else pe.binding.address,
                      dst=pe.binding.address if read else address, index=index,
                      data=[DimSlice(start=0, end=size)], dma_command_mode=mode)
    return message, endpoint, pe


def post(message, endpoint, pe, read, mode, adapted):
    if adapted:
        endpoint, pe = LegacyDMAAdapter(endpoint), LegacyDMAAdapter(pe)
    tasks = [pe.recv_message(message, NMCShapeMode.DYNAMIC) if read else pe.send(message)]
    if mode is DMACommandMode.DUAL_SIDE:
        tasks.append(endpoint.send(message) if read else endpoint.recv_message(message))
    return tasks


class LegacyDMAAdapterTests(unittest.TestCase):
    assert_drained = dma_tests.DMAConfigurationTests.assert_drained

    def capture(self, env, arch, cores, tasks, adapted):
        env.run(until=env.all_of(tasks))
        completed_at = env.now
        results = [t.value for t in tasks]
        if adapted:
            for result in results:
                self.assertIsInstance(result, LegacyDMAResult)
                record, native = result.lifecycle, result.native
                self.assertEqual(record.execution_policy, "legacy_dma")
                self.assertEqual(record.submission_aci_cycles, native.submission_time_aci_cycles)
                self.assertEqual(record.descriptor_acceptance_aci_cycles, native.descriptor_acceptance_time_aci_cycles)
                self.assertEqual(record.completion_aci_cycles, native.operation_completion_time_aci_cycles)
                if record.operation == "send":
                    self.assertEqual(record.final_local_handoff_aci_cycles, native.final_local_handoff_time_aci_cycles)
                    self.assertIsNone(record.tail_service_completion_aci_cycles)
                else:
                    tail = (native.tail_service_completion_time_aci_cycles if record.client == "dma_endpoint"
                            else native.tail_rx_service_completion_time_aci_cycles)
                    self.assertEqual(record.tail_service_completion_aci_cycles, tail)
                    self.assertIsNone(record.final_local_handoff_aci_cycles)
                self.assertIsNone(record.source_read_completion_aci_cycles)
                self.assertIsNone(record.destination_ready_aci_cycles)
                self.assertIsNone(record.response_receipt_aci_cycles)
                self.assertEqual(record, LegacyDMALifecycle.model_validate_json(record.model_dump_json()))
            results = [r.native for r in results]
        env.run()
        self.assert_drained(arch, cores)
        return (results, completed_at, env.now, collect_dma_service_events(arch.dma_endpoints),
                {fabric: list(noc.tracer.events) for fabric, noc in arch.nocs.items()})

    def test_exact_matrix_packets_issuers_finite_slots_and_drain(self):
        # 32 paired runs, each with six concurrent commands on both fabrics.
        for kind, node_type, read in dma_tests.KINDS:
            for mode in DMACommandMode:
                for capacity, physical in ((7, 9), (19, 19)):
                    for separate in (False, True):
                        with self.subTest(kind=kind, mode=mode, capacity=capacity, separate=separate):
                            captures = []
                            for adapted in (False, True):
                                env, arch, cores = dma_tests.dma_runtime(
                                    kind, read, mode, capacity=capacity, physical=physical,
                                    issuers=separate, descriptor_slots=1)
                                tasks = []
                                for index in range(3):
                                    for fabric in arch.nocs:
                                        msg, endpoint, pe = command(arch, cores, node_type, read, mode, fabric,
                                                                    index=index, size=capacity * 3 + 1)
                                        tasks.extend(post(msg, endpoint, pe, read, mode, adapted))
                                captures.append(self.capture(env, arch, cores, tasks, adapted))
                            self.assertEqual(*captures)
                            for result in captures[1][0]:
                                self.assertEqual([f.payload_bytes for f in result.flits], [capacity] * 3 + [1])
                                self.assertTrue(all(f.transfer_bytes == physical for f in result.flits))

    def test_adapter_delegates_once_and_retains_native_result(self):
        for kind, node_type, read in dma_tests.KINDS:
            env, arch, cores = dma_tests.dma_runtime(kind, read, DMACommandMode.DUAL_SIDE)
            msg, endpoint, pe = command(arch, cores, node_type, read, DMACommandMode.DUAL_SIDE, NoCChannel.CH0)
            method = "send" if read else "recv_message"
            original = getattr(endpoint, method)
            native_tasks = []

            def observe(message, original=original, native_tasks=native_tasks):
                native_tasks.append(original(message))
                return native_tasks[-1]

            with patch.object(endpoint, method, side_effect=observe) as delegate:
                tasks = post(msg, endpoint, pe, read, DMACommandMode.DUAL_SIDE, True)
                self.capture(env, arch, cores, tasks, True)
                delegate.assert_called_once_with(msg)
                self.assertIs(tasks[-1].value.native, native_tasks[0].value)

    def test_dual_side_rendezvous_does_not_post_a_peer(self):
        for kind, node_type, read in dma_tests.KINDS:
            captures = []
            for adapted in (False, True):
                env, arch, cores = dma_tests.dma_runtime(kind, read, DMACommandMode.DUAL_SIDE)
                msg, endpoint, pe = command(arch, cores, node_type, read, DMACommandMode.DUAL_SIDE, NoCChannel.CH0)
                sender, receiver = (endpoint, pe) if read else (pe, endpoint)
                if adapted:
                    sender, receiver = LegacyDMAAdapter(sender), LegacyDMAAdapter(receiver)
                tx = sender.send(msg)
                env.run(until=10)
                self.assertFalse(tx.triggered)
                self.assertFalse(endpoint.service_events)
                self.assertTrue(all(not noc.tracer.events for noc in arch.nocs.values()))
                rx = receiver.recv_message(msg, NMCShapeMode.STATIC) if read else receiver.recv_message(msg)
                captures.append(self.capture(env, arch, cores, (tx, rx), adapted))
            self.assertEqual(*captures)

    def test_mid_transfer_fault_and_recovery_match_native_service(self):
        for kind, node_type, read in dma_tests.KINDS:
            for mode in DMACommandMode:
                captures = []
                for adapted in (False, True):
                    env, arch, cores = dma_tests.dma_runtime(kind, read, mode, bandwidth=1)
                    msg, endpoint, pe = command(arch, cores, node_type, read, mode, NoCChannel.CH1, size=161)

                    def fault(env=env, endpoint=endpoint):
                        yield env.timeout(20)
                        endpoint.scale_service_delay(3)
                        yield env.timeout(50)
                        endpoint.scale_service_delay(1 / 3)

                    env.process(fault())
                    tasks = post(msg, endpoint, pe, read, mode, adapted)
                    captures.append(self.capture(env, arch, cores, tasks, adapted))
                self.assertEqual(*captures)
                service = captures[0][3][node_type, 17]
                durations = {e.end_time - e.start_time for e in service[:-1]}
                # Legacy port_bw is physical bytes/ACI cycle: 9/1, then 3x.
                # Endpoint clocks apply to issue timing, not this byte rate.
                self.assertEqual(durations, {9, 27})
                self.assertEqual((service[0].delay_factor, service[-1].delay_factor), (1, 1))

    def test_rejection_precedes_native_admission(self):
        kind, node_type, read = dma_tests.KINDS[0]
        env, arch, cores = dma_tests.dma_runtime(kind, read, DMACommandMode.SINGLE_SIDE)
        msg, endpoint, pe = command(arch, cores, node_type, read, DMACommandMode.SINGLE_SIDE, NoCChannel.CH0)
        adapter, pe_adapter = LegacyDMAAdapter(endpoint), LegacyDMAAdapter(pe)
        with self.assertRaises(TypeError):
            LegacyDMAAdapter(object())
        with self.assertRaises(ValueError):
            adapter.send(msg)  # Single-side reads originate at the PE.
        with self.assertRaises(ValueError):
            pe_adapter.recv_message(msg)
        with self.assertRaises(ValueError):
            adapter.recv_message(msg, NMCShapeMode.STATIC)
        fixed = msg.model_copy(update={"trans_type": TransType.FIXPATH, "fixed_path": (2, 5)})
        local = msg.model_copy(update={"src": msg.src.model_copy(update={"attachment_mode": DMAAttachmentMode.LOCAL})})
        pe_only = Message(src=pe.binding.address, dst=cores[4].nmc_channel_for(NoCChannel.CH0).binding.address,
                          index=2, data=[DimSlice(start=0, end=1)])
        for rejected in (fixed, local, pe_only):
            with self.subTest(message=rejected), self.assertRaises(NotImplementedError):
                pe_adapter.recv_message(rejected, NMCShapeMode.DYNAMIC)
        env.run()
        self.assert_drained(arch, cores)
        self.assertFalse(endpoint.service_events)
        self.assertTrue(all(not noc.tracer.events for noc in arch.nocs.values()))


def buffer(name="a", address=0, size=5, resource="scratchpad"):
    return MemoryBuffer(buffer_id=name, resource_id=resource, base_address=address, size_bytes=size)


def scratchpad(size=13, delay=3):
    env = simpy.Environment()
    return env, ScratchpadMemory(env, 37, SPMConfig(size=size, delay=delay))


class ScratchpadCapacityAdapterTests(unittest.TestCase):
    def bind(self, env, spm):
        return ScratchpadCapacityAdapter(env, spm, resource_id="scratchpad", capacity_bytes=int(spm.container.capacity))

    def assert_snapshot(self, adapter, used, pending, states):
        state = adapter.snapshot()
        self.assertEqual(state.used_bytes, used)
        self.assertEqual(state.pending_operations, pending)
        self.assertEqual(state.available_bytes + used, state.capacity_bytes)
        self.assertEqual([r.state for r in state.reservations], states)
        self.assertEqual(state, ScratchpadCapacityState.model_validate_json(state.model_dump_json()))

    def test_original_container_delay_charged_once_and_drained_detach(self):
        for delay in (0, 3, 7):
            env, spm = scratchpad(delay=delay)
            original_container = spm.container
            with (patch("simpy.Container", side_effect=AssertionError("shadow container")),
                  patch.object(spm.container, "get", wraps=spm.container.get) as get,
                  patch.object(spm.container, "put", wraps=spm.container.put) as put):
                adapter = self.bind(env, spm)
                reserved = adapter.reserve(buffer(), task_index=11)
                self.assertNotIsInstance(reserved, simpy.Process)
                self.assert_snapshot(adapter, 0, 1, ["allocating"])
                self.assertIs(spm.container, original_container)
                with self.assertRaises(ValueError):
                    adapter.detach()
                handle = env.run(until=reserved)
                self.assertEqual(env.now, delay)
                self.assertEqual(spm.pending_capacity_operations, 0)
                get.assert_called_once_with(5)
                self.assert_snapshot(adapter, 5, 0, ["reserved"])
                with self.assertRaises(ValueError):
                    adapter.detach()
                released = adapter.release(handle, task_index=11)
                self.assert_snapshot(adapter, 5, 1, ["releasing"])
                with self.assertRaises(ValueError):
                    adapter.detach()
                env.run(until=released)
                self.assertEqual(env.now, 2 * delay)
                put.assert_called_once_with(5)
                self.assert_snapshot(adapter, 0, 0, [])
                self.assertEqual([(e.action, e.time_aci_cycles, e.available_bytes) for e in adapter.events],
                                 [("reserve", delay, 8), ("release", 2 * delay, 13)])
                self.assertTrue(all(e.version is None and e.access_id is None for e in adapter.events))
                env.run()
                adapter.detach()
                self.assertFalse(adapter.snapshot().attached)
            self.assertFalse(spm.container.get_queue)
            self.assertFalse(spm.container.put_queue)
            env.run(until=env.process(spm.allocate(13, 12)))
            env.run(until=env.process(spm.release(13, 12)))
            self.assertEqual(env.now, 4 * delay)
            self.assertEqual(spm.used_bytes, 0)
            for call, args in ((adapter.detach, ()), (adapter.reserve, (buffer(),)), (adapter.release, (handle,))):
                with self.assertRaises(ValueError):
                    call(*args)

    def test_concurrent_reservations_pending_snapshot_and_physical_ranges(self):
        env, spm = scratchpad()
        adapter = self.bind(env, spm)
        first, second = adapter.reserve(buffer()), adapter.reserve(buffer("b", 5, 8))
        self.assert_snapshot(adapter, 0, 2, ["allocating", "allocating"])
        for invalid in (buffer(), buffer("overlap", 4, 2), buffer("overrun", 13, 1), buffer("foreign", resource="elsewhere")):
            with self.assertRaises(ValueError):
                adapter.reserve(invalid)
        env.run(until=2)
        self.assertEqual(spm.pending_capacity_operations, 2)
        self.assert_snapshot(adapter, 0, 2, ["allocating", "allocating"])
        with self.assertRaises(ValueError):
            adapter.release(BufferHandle(buffer()))
        env.run(until=env.all_of((first, second)))
        self.assertEqual(env.now, 3)
        self.assert_snapshot(adapter, 13, 0, ["reserved", "reserved"])
        with self.assertRaises(ValueError):
            adapter.release(BufferHandle(first.value.buffer))
        released = adapter.release(first.value)
        with self.assertRaises(ValueError):
            adapter.release(first.value)
        with self.assertRaises(ValueError):
            adapter.reserve(buffer("reused", 0, 5))
        env.run(until=released)
        with self.assertRaises(ValueError):
            adapter.release(first.value)
        new = adapter.reserve(buffer("reused", 0, 5))
        env.run(until=new)
        env.run(until=env.all_of((adapter.release(new.value), adapter.release(second.value))))
        env.run()
        self.assert_snapshot(adapter, 0, 0, [])
        self.assertEqual(spm.pending_capacity_operations, 0)
        adapter.detach()

    def test_bound_owner_rejects_unmanaged_operations_without_work(self):
        env, spm = scratchpad()
        adapter = self.bind(env, spm)
        for action in (lambda: self.bind(env, spm), lambda: spm.allocate(1, 1), lambda: spm.release(1, 1),
                       lambda: spm.allocate(1, 1, owner=object()), lambda: spm.unbind_capacity_owner(object())):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(env.peek(), float("inf"))
        self.assertEqual(spm.pending_capacity_operations, 0)
        self.assert_snapshot(adapter, 0, 0, [])
        handle = env.run(until=adapter.reserve(buffer()))
        with self.assertRaises(ValueError):
            spm.release(5, 1)
        self.assertEqual(spm.used_bytes, 5)
        env.run(until=adapter.release(handle))
        env.run()
        adapter.detach()

    def test_binding_requires_matching_environment_capacity_and_valid_delay(self):
        env, spm = scratchpad()
        for other_env, capacity, resource in ((simpy.Environment(), 13, "scratchpad"), (env, 12, "scratchpad"), (env, 13, "")):
            with self.assertRaises(ValueError):
                ScratchpadCapacityAdapter(other_env, spm, resource_id=resource, capacity_bytes=capacity)
        self.bind(env, spm).detach()  # Failed constructors must not claim ownership.
        env, spm = scratchpad(delay=-1)
        with self.assertRaisesRegex(ValueError, "delay"):
            self.bind(env, spm)

    def test_binding_rejects_unstarted_pending_occupied_and_queued_legacy_work(self):
        env, spm = scratchpad()
        allocating = spm.allocate(5, 1)
        with self.assertRaises(ValueError):
            self.bind(env, spm)
        task = env.process(allocating)
        env.run(until=1)
        with self.assertRaises(ValueError):
            self.bind(env, spm)
        env.run(until=task)
        with self.assertRaises(ValueError):
            self.bind(env, spm)
        releasing = env.process(spm.release(5, 1))
        with self.assertRaises(ValueError):
            self.bind(env, spm)
        env.run(until=releasing)
        self.bind(env, spm).detach()
        # Raw SimPy requests already queued against a full, unused container
        # also make binding unsafe, even if no public SPM method was called.
        for method, size in (("get", 14), ("put", 1)):
            request = getattr(spm.container, method)(size)
            self.assertFalse(request.triggered)
            with self.assertRaises(ValueError):
                self.bind(env, spm)
            request.cancel()
            self.bind(env, spm).detach()

    def test_unbound_legacy_capacity_waits_and_error_recovery(self):
        env, spm = scratchpad()
        first = env.process(spm.allocate(13, 1))
        second = env.process(spm.allocate(5, 2))
        env.run(until=first)
        env.run(until=4)
        self.assertFalse(second.triggered)
        release = env.process(spm.release(5, 1))
        env.run(until=env.all_of((release, second)))
        self.assertEqual(env.now, 7)
        self.assertEqual(spm.used_bytes, 13)
        self.assertEqual(spm.pending_capacity_operations, 0)
        env.run(until=env.process(spm.release(13, 2)))
        self.assertEqual(env.now, 10)
        with self.assertRaises(RuntimeError):
            env.run(until=env.process(spm.allocate(-1, 3)))
        self.assertEqual(spm.pending_capacity_operations, 0)
        self.bind(env, spm).detach()

    def test_legacy_load_store_tasks_on_example_cores_keep_capacity_and_lsu_costs(self):
        path = Path(__file__).parents[1] / "configs/instances/mesh_example.json"
        config = ArchConfig.model_validate_json(path.read_text())
        config.core.spm = SPMConfig(size=13, delay=2)
        config.core.lsu.width = 2.5
        for operation in (OperatorType.LOAD_FEAT, OperatorType.LOAD_WGT):
            env, arch, cores = dma_tests.build_runtime(config.noc, config.core)
            core = cores[0]
            shape = [DimSlice(start=0, end=13)]
            load = Task(DFGNode(1, operation, 0, input_size=shape, weight_size=shape))
            store = Task(DFGNode(2, OperatorType.STORE, 0, output_size=shape))
            env.run(until=env.process(load.execute(core)))
            # Existing LSU floors 13/2.5 to 5 cycles; one 2-cycle allocation.
            self.assertEqual((env.now, core.spm.used_bytes), (7, 13))
            env.run(until=env.process(store.execute(core)))
            self.assertEqual((env.now, core.spm.used_bytes), (14, 0))
            self.assertEqual(core.spm.pending_capacity_operations, 0)
            self.assertFalse(core.lsu.resource.users)
            self.assertFalse(core.lsu.resource.queue)
            self.assertTrue(all(not noc.tracer.events for noc in arch.nocs.values()))
            self.bind(env, core.spm).detach()


class MemoryConsumerBoundaryTests(unittest.TestCase):
    def test_memory_models_documents_and_traces_fail_legacy_admission(self):
        doc, graph = documents()
        replay = MemoryReplay.model_validate(doc)
        result = MemoryRuntime(execution_plan(doc, graph)).run()
        exported = result.model_dump(mode="json")
        for value in (replay, doc, result, exported):
            for consumer in ("detailed_predictor", "detailed_encoder"):
                with self.assertRaises(TypeError):
                    require_legacy_topology(value, consumer)
            with self.assertRaises(TypeError):
                require_legacy_nocs(value)
            with self.assertRaises(TypeError):
                build_hardware_graph(value)
            with self.assertRaises(TypeError):
                detect(1, 1, value, [], [])
            for core_events, link_events in ((value, []), ([], value)):
                with self.assertRaises((TypeError, ValueError)):
                    detect(1, 1, ArchConfig(), core_events, link_events)
            for stream in ("compute", "communication"):
                with self.assertRaises((TypeError, ValueError)):
                    legacy_event_rows(value, stream)
        for records in (exported["operations"], exported["chunks"], exported["ownership_trace"], exported["transport"]["trace"]):
            self.assertTrue(records)
            for value in (records, {"trace": records}):
                for stream in ("compute", "communication"):
                    with self.assertRaises(ValueError):
                        legacy_event_rows(value, stream)
        for stream, ids in (("compute", {"pe_id": 0}), ("communication", {"src_id": 0, "dst_id": 1})):
            rows = [{"start_time": 0, "end_time": 1, **ids}]
            self.assertEqual(legacy_event_rows({"trace": rows}, stream), rows)

    def test_admission_import_requires_no_tensor_or_checkpoint_dependency(self):
        # Exercise the real shared admission boundary in a fresh interpreter.
        # Optional detector/encoder inference is deliberately not substituted.
        script = '''
import importlib.abc
import json
import sys
class NoTensors(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"torch", "torch_geometric"}:
            raise AssertionError("tensor import before admission")
sys.meta_path.insert(0, NoTensors())
from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.embedding.hw_encoder import build_hardware_graph
from simulator_detailed.predictor.predict import detect
from simulator_detailed.topology_compatibility import legacy_event_rows, require_legacy_nocs
doc = json.loads(sys.stdin.read())
for guard, args in ((legacy_event_rows, (doc, "communication")), (require_legacy_nocs, (doc,)),
                    (build_hardware_graph, (doc,)), (detect, (1, 1, doc, [], [])),
                    (detect, (1, 1, ArchConfig(), doc, [])), (detect, (1, 1, ArchConfig(), [], doc))):
    try:
        guard(*args)
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("memory replay accepted")
'''
        result = subprocess.run([sys.executable, "-c", script], input=json.dumps(documents()[0]),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("torch", "torch_geometric")),
                         "optional Torch/PyG are unavailable; no substitute tensor execution")
    def test_optional_encoder_public_class_graph_shape_and_state_dict(self):
        from simulator_detailed.embedding import hw_encoder
        from simulator_detailed.embedding.hw_encoder import HardwareEmbedding

        self.assertIs(hw_encoder.HardwareEmbedding, HardwareEmbedding)
        self.assertIs(pickle.loads(pickle.dumps(HardwareEmbedding)), HardwareEmbedding)
        self.assertEqual(HardwareEmbedding.__module__, hw_encoder.__name__)
        _, arch, _ = dma_tests.build_runtime(ArchConfig().noc)
        nodes, edges = build_hardware_graph(arch.nocs)
        node_count = sum(len(noc.routers) + len(noc.r2r_links) for noc in arch.nocs.values())
        edge_count = 2 * sum(len(noc.r2r_links) for noc in arch.nocs.values())
        self.assertEqual(tuple(nodes.shape), (node_count, 4))
        self.assertEqual(tuple(edges.shape), (2, edge_count))
        model = HardwareEmbedding(node_count)
        self.assertEqual(set(model.state_dict()), {"conv1.bias", "conv1.lin.weight", "conv2.bias", "conv2.lin.weight",
                                                  "lin.weight", "lin.bias"})
        restored = HardwareEmbedding(node_count)
        restored.load_state_dict(model.state_dict(), strict=True)
        self.assertEqual(tuple(restored(nodes, edges).shape), (node_count, 1))


if __name__ == "__main__":
    unittest.main()
