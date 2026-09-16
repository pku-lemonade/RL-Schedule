"""Independent capacity, range-version, service-cost and contention oracles."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import simpy

from simulator_detailed.configs.schemas.memory_replay import (
    MemoryBuffer,
    MemoryReplay,
    MemoryServiceConfig,
)
from simulator_detailed.memory_plan import MemoryPlan
from simulator_detailed.memory_resources import (
    MemoryAccess,
    MemoryResource,
    MemoryResourceDefinition,
    MemoryResourcePlan,
    MemoryResources,
    MemoryResourceState,
    MemoryVersion,
)
from simulator_detailed.memory_service import (
    MemoryChunkRecord,
    MemoryService,
    ServiceChunk,
    ServiceTiming,
)
from simulator_detailed.tests.test_memory_packets import EVIDENCE, memory_documents


def timing(*, granule=32, chunk=32, bandwidth=32, latency=1, native=1_000_000_000,
           aci=500_000_000, queue=1):
    return ServiceTiming(aci_clock_hz=aci, config=MemoryServiceConfig(
        policy="aggregate_shared_rw_v1", native_clock_hz=native, service_granule_bytes=granule,
        chunk_bytes=chunk, bytes_per_cycle=bandwidth, fixed_latency_cycles=latency,
        queue_capacity=queue, evidence=EVIDENCE))


def resource(*, capacity=128, service=None):
    env = simpy.Environment()
    return env, MemoryResource(env, MemoryResourceDefinition(resource_id="l1", capacity_bytes=capacity,
                                                            timing=service or timing()))


def buffer(name="b", address=0, size=64, ready=False, **values):
    return MemoryBuffer(buffer_id=name, resource_id="l1", base_address=address, size_bytes=size,
                        initially_ready=ready, **values)


def version(producer=None):
    return MemoryVersion(kind="initial" if producer is None else "producer", producer_id=producer)


def access(direction="read", offset=0, size=32, producer=None, client="client"):
    return MemoryAccess(client_id=client, direction=direction, offset_bytes=offset, size_bytes=size,
                        version=version(producer))


def registry():
    document, graph = memory_documents("write_acknowledged", 33)
    document["buffers"][1]["initially_ready"] = True
    plan = MemoryResourcePlan.compile(MemoryPlan.compile(MemoryReplay.model_validate(document), graph))
    env = simpy.Environment()
    return env, MemoryResources(env, plan)


class MemoryOwnershipTests(unittest.TestCase):
    def test_aliases_share_one_capacity_and_teardown_restores_once(self):
        env, owners = registry()
        self.assertIs(owners.via("src0", "l1-a"), owners.via("src1", "l1-a"))
        self.assertIs(owners.via("ram0", "dram"), owners.via("ram1", "dram"))
        for owner in owners.resources.values():
            state = owner.snapshot()
            self.assertEqual((state.capacity_bytes, state.reserved_bytes, state.available_bytes), (65536, 16384, 49152))
            self.assertTrue(owner.is_drained)
            self.assertEqual(state, MemoryResourceState.model_validate_json(state.model_dump_json()))
            self.assertEqual(len(owner.events), 1)
        with self.assertRaises(ValueError):
            owners.via("src0", "dram")
        owners.teardown()
        env.run()
        self.assertTrue(owners.is_drained)
        self.assertTrue(all(r.available_bytes == 65536 and not r.snapshot().buffers for r in owners.resources.values()))
        with self.assertRaises(ValueError):
            owners.teardown()
        self.assertTrue(all(len(r.events) == 2 for r in owners.resources.values()))

    def test_capacity_handle_identity_overlap_and_rejection_before_mutation(self):
        env, owner = resource()
        first = owner.reserve(buffer(size=32))
        second = owner.reserve(buffer("second", 32, 96))
        self.assertEqual(owner.available_bytes, 0)
        before, events = owner.snapshot(), owner.events
        for candidate in (buffer("overlap", 16, 32), buffer("past", 128, 1), buffer(size=32),
                          buffer("other").model_copy(update={"resource_id": "other"}),
                          buffer("bad").model_copy(update={"size_bytes": True})):
            with self.assertRaises(ValueError):
                owner.reserve(candidate)
            self.assertEqual(owner.snapshot(), before)
            self.assertEqual(owner.events, events)
        with self.assertRaises(ValueError):
            owner.release(replace(first))
        owner.release(first)
        with self.assertRaises(ValueError):
            owner.release(first)
        owner.release(second)
        env.run()
        self.assertEqual(owner.available_bytes, 128)
        self.assertEqual(owner.snapshot().peak_reserved_bytes, 128)

    def test_resource_compilation_rejects_overlap_geometry_and_extreme_costs_without_runtime(self):
        def overlap(doc):
            doc["buffers"].append({**doc["buffers"][0], "buffer_id": "overlap", "base_address": 32})
        mutations = (
            overlap,
            lambda d: d["resources"][1].update(capacity_override_bytes=32),
            lambda d: d["resources"][0]["service"].update(chunk_bytes=64),
            lambda d: d["resources"][0]["service"].update(service_granule_bytes=24, chunk_bytes=24),
            lambda d: d["resources"][0]["service"].update(bytes_per_cycle=1e-308),
            lambda d: d["resources"][0]["service"].update(native_clock_hz=1e-308),
        )
        for mutate in mutations:
            doc, graph = memory_documents()
            mutate(doc)
            with self.subTest(mutate=mutate), patch("simpy.Container", side_effect=AssertionError("allocated")), self.assertRaises(ValueError):
                MemoryResourcePlan.compile(MemoryPlan.compile(MemoryReplay.model_validate(doc), graph))

    def test_teardown_preflights_all_owners_and_opaque_handles(self):
        env, owners = registry()
        local = owners.resources["l1-a"]
        handle = owners.handles["local"]
        lease = local.try_acquire(handle, access())
        with self.assertRaises(ValueError):
            owners.teardown()
        self.assertEqual(owners.resources["dram"].snapshot().reserved_bytes, 16384)
        local.release_access(lease)
        local.release(handle)
        replacement = local.reserve(handle.buffer)
        before = {key: r.snapshot() for key, r in owners.resources.items()}
        with self.assertRaises(ValueError):
            owners.teardown()
        self.assertEqual({key: r.snapshot() for key, r in owners.resources.items()}, before)
        local.release(replacement)
        env.run()

    def test_uninitialized_partial_versions_overwrite_and_access_conflicts(self):
        env, owner = resource()
        handle = owner.reserve(buffer(producer_operation_id="producer"))
        self.assertIsNone(owner.try_acquire(handle, access(producer="producer")))
        write = owner.try_acquire(handle, access("write", size=33, producer="producer"))
        self.assertIsNotNone(write)
        self.assertIsNone(owner.try_acquire(handle, access("write", producer="other")))
        a = owner.try_service(write, service_id="first", offset_bytes=0, size_bytes=32)
        b = owner.try_service(write, service_id="last", offset_bytes=32, size_bytes=1)
        self.assertFalse(owner.is_ready(handle, offset_bytes=0, size_bytes=32, version=version("producer")))
        for action in (lambda: owner.release(handle), lambda: owner.release_access(write),
                       lambda: owner.release_access(replace(write))):
            with self.assertRaises(ValueError):
                action()
        env.run(until=a)
        self.assertTrue(owner.is_ready(handle, offset_bytes=0, size_bytes=32, version=version("producer")))
        self.assertFalse(owner.is_ready(handle, offset_bytes=0, size_bytes=33, version=version("producer")))
        self.assertFalse(owner.access_complete(write))
        self.assertIsNone(owner.try_acquire(handle, access(producer="producer")))
        env.run(until=b)
        self.assertTrue(owner.access_complete(write))
        owner.release_access(write)
        self.assertEqual([(r.address, r.size_bytes) for r in owner.snapshot().buffers[0].ready_ranges], [(0, 33)])
        read_a = owner.try_acquire(handle, access(size=33, producer="producer", client="a"))
        read_b = owner.try_acquire(handle, access(size=33, producer="producer", client="b"))
        self.assertIsNotNone(read_a)
        self.assertIsNotNone(read_b)
        disjoint = owner.try_acquire(handle, access("write", offset=40, size=8, producer="disjoint"))
        self.assertIsNotNone(disjoint)
        self.assertIsNone(owner.try_acquire(handle, access("write", size=1, producer="next")))
        owner.release_access(read_a)
        owner.release_access(read_b)
        owner.release_access(disjoint)
        overwrite = owner.try_acquire(handle, access("write", size=1, producer="next"))
        self.assertFalse(owner.is_ready(handle, offset_bytes=0, size_bytes=1, version=version("producer")))
        done = owner.try_service(overwrite, service_id="overwrite", offset_bytes=0, size_bytes=1)
        env.run(until=done)
        owner.release_access(overwrite)
        self.assertIsNone(owner.try_acquire(handle, access(size=33, producer="producer")))
        self.assertTrue(owner.is_ready(handle, offset_bytes=1, size_bytes=32, version=version("producer")))
        self.assertTrue(owner.is_ready(handle, offset_bytes=0, size_bytes=1, version=version("next")))
        self.assertFalse(owner.is_ready(handle, offset_bytes=33, size_bytes=1, version=version("producer")))
        self.assertEqual(sum(r.serviced_bytes for r in owner.service.records), 96)
        owner.release(handle)

    def test_initial_version_permissions_and_mutated_access_are_strict(self):
        _, owner = resource()
        handle = owner.reserve(buffer(ready=True, writable=False))
        initial = owner.try_acquire(handle, access())
        self.assertIsNotNone(initial)
        self.assertIsNone(owner.try_acquire(handle, access(producer="not-produced")))
        before = owner.snapshot(), owner.events
        for request in (access("write", producer="writer"), access(offset=63, size=2),
                        access().model_copy(update={"size_bytes": True})):
            with self.assertRaises(ValueError):
                owner.try_acquire(handle, request)
            self.assertEqual((owner.snapshot(), owner.events), before)
        owner.release_access(initial)
        with self.assertRaises(ValueError):
            owner.release_access(initial)

    def test_queued_service_pins_access_and_saturation_has_no_hidden_job(self):
        env, owner = resource(service=timing(latency=9))
        handle = owner.reserve(buffer(size=96, ready=True))
        lease = owner.try_acquire(handle, access(size=96))
        tickets = [owner.try_service(lease, service_id=f"chunk-{i}", offset_bytes=i * 32, size_bytes=32) for i in range(3)]
        self.assertIsNone(tickets[2])
        state, events = owner.snapshot(), owner.service.events
        self.assertEqual((state.service.active, state.service.queued), (1, 1))
        self.assertIsNone(owner.try_service(lease, service_id="chunk-2", offset_bytes=64, size_bytes=32))
        self.assertEqual((owner.snapshot(), owner.service.events), (state, events))
        with self.assertRaises(ValueError):
            owner.try_service(lease, service_id="duplicate-extent", offset_bytes=0, size_bytes=32)
        with self.assertRaises(ValueError):
            owner.try_service(lease, service_id="bad", offset_bytes=96, size_bytes=1)
        with self.assertRaises(ValueError):
            owner.release_access(lease)
        env.run(until=tickets[0])
        last = owner.try_service(lease, service_id="chunk-2", offset_bytes=64, size_bytes=32)
        env.run(until=last)
        self.assertTrue(owner.access_complete(lease))
        self.assertEqual([(r.start_aci_cycles, r.end_aci_cycles) for r in owner.service.records], [(0, 5), (5, 10), (10, 15)])
        owner.release_access(lease)
        self.assertTrue(owner.is_drained)
        owner.release(handle)

    def test_middle_overwrite_and_reversed_chunks_preserve_exact_ready_ranges(self):
        env, owner = resource(service=timing(granule=8, chunk=16))
        handle = owner.reserve(buffer(ready=True))
        write = owner.try_acquire(handle, access("write", offset=16, size=32, producer="middle"))
        self.assertEqual([(r.address, r.size_bytes, r.version.kind) for r in owner.snapshot().buffers[0].ready_ranges],
                         [(0, 16, "initial"), (48, 16, "initial")])
        later = owner.try_service(write, service_id="later", offset_bytes=16, size_bytes=16)
        earlier = owner.try_service(write, service_id="earlier", offset_bytes=0, size_bytes=16)
        env.run(until=later)
        self.assertTrue(owner.is_ready(handle, offset_bytes=32, size_bytes=16, version=version("middle")))
        self.assertFalse(owner.is_ready(handle, offset_bytes=16, size_bytes=32, version=version("middle")))
        env.run(until=earlier)
        owner.release_access(write)
        self.assertEqual([(r.address, r.size_bytes, r.version.kind) for r in owner.snapshot().buffers[0].ready_ranges],
                         [(0, 16, "initial"), (16, 32, "producer"), (48, 16, "initial")])
        self.assertFalse(owner.is_ready(handle, offset_bytes=0, size_bytes=64, version=version()))
        self.assertTrue(owner.is_ready(handle, offset_bytes=16, size_bytes=32, version=version("middle")))
        # Resource identity, rather than matching textual buffer/access IDs,
        # decides whether a handle or lease belongs to an owner.
        _, foreign = resource()
        foreign_handle = foreign.reserve(buffer(ready=True))
        foreign_lease = foreign.try_acquire(foreign_handle, access())
        for operation in (lambda: owner.release(foreign_handle),
                          lambda: owner.try_acquire(foreign_handle, access()),
                          lambda: owner.try_service(foreign_lease, service_id="foreign", offset_bytes=0, size_bytes=1)):
            with self.assertRaises(ValueError):
                operation()


class MemoryServiceTests(unittest.TestCase):
    def test_two_independent_native_clock_and_rounded_service_tables(self):
        # Literal expected costs/times: not computed with production helpers.
        cases = (
            (timing(queue=2), ((0, 1, 32, 2, 0, 1), (32, 32, 32, 2, 1, 2), (63, 2, 64, 3, 2, 3.5))),
            (timing(granule=8, chunk=16, bandwidth=4, latency=0.5, native=250_000_000, aci=1_000_000_000, queue=2),
             ((0, 1, 8, 2.5, 0, 10), (16, 16, 16, 4.5, 10, 28), (7, 2, 16, 4.5, 28, 46))),
        )
        for settings, table in cases:
            env = simpy.Environment()
            service = MemoryService(env, "physical", settings)
            for i, (address, size, *_expected) in enumerate(table):
                self.assertIsNotNone(service.try_submit(ServiceChunk(service_id=str(i), client_id=f"alias-{i}",
                                                                    direction="write" if i == 1 else "read", address=address,
                                                                    useful_bytes=size)))
            env.run()
            self.assertTrue(service.is_drained)
            for row, record in zip(table, service.records, strict=True):
                self.assertEqual((record.address, record.useful_bytes, record.serviced_bytes, record.native_cycles,
                                  record.start_aci_cycles, record.end_aci_cycles), row)
                self.assertEqual(record, MemoryChunkRecord.model_validate_json(record.model_dump_json()))
            self.assertEqual([r.client_id for r in service.records], ["alias-0", "alias-1", "alias-2"])
            for event in service.events:
                self.assertLessEqual(event.active, 1)
                self.assertLessEqual(event.queued, 2)
            self.assertEqual(service.snapshot().completed_read_bytes, table[0][2] + table[2][2])
            self.assertEqual(service.snapshot().completed_write_bytes, table[1][2])

    def test_positive_subquantum_work_and_invalid_admission(self):
        env = simpy.Environment()
        service = MemoryService(env, "r", timing(granule=1, chunk=8, bandwidth=16, latency=0, native=1, aci=1))
        good = ServiceChunk(service_id="byte", client_id="local", direction="read", address=0, useful_bytes=1)
        for update in ({"useful_bytes": 0}, {"useful_bytes": 9}, {"address": -1}, {"useful_bytes": True}):
            with self.assertRaises(ValueError):
                service.try_submit(good.model_copy(update=update))
            self.assertFalse(service.events)
            self.assertTrue(service.is_drained)
        ticket = service.try_submit(good)
        before = service.snapshot(), service.events
        with self.assertRaises(ValueError):
            service.try_submit(good)
        self.assertEqual((service.snapshot(), service.events), before)
        env.run(until=ticket)
        self.assertEqual(env.now, 0.0625)

    def test_dual_fabric_alias_and_local_clients_share_service_independent_resources_overlap(self):
        env, owners = registry()
        target = owners.resources["dram"]
        local = owners.resources["l1-a"]
        jobs = [
            (owners.via("ram0", "dram"), owners.handles["remote"], access(client="fabric-0")),
            (owners.via("ram1", "dram"), owners.handles["remote"], access("write", 32, producer="writer", client="fabric-1")),
            (target, owners.handles["remote"], access(offset=64, client="local-client")),
            (local, owners.handles["local"], access(client="independent")),
        ]
        completions = []
        def client(index, owner, handle, request):
            lease = owner.try_acquire(handle, request)
            self.assertIsNotNone(lease)
            ticket = owner.try_service(lease, service_id=f"job-{index}", offset_bytes=0, size_bytes=32)
            while ticket is None:
                yield owner.service.changed
                ticket = owner.try_service(lease, service_id=f"job-{index}", offset_bytes=0, size_bytes=32)
            yield ticket
            owner.release_access(lease)
            completions.append((index, env.now))
        for i, job in enumerate(jobs):
            env.process(client(i, *job))
        env.run()
        self.assertEqual([(r.client_id, r.start_aci_cycles, r.end_aci_cycles) for r in target.service.records],
                         [("fabric-0", 0, 1), ("fabric-1", 1, 2), ("local-client", 2, 3)])
        self.assertEqual([(r.start_aci_cycles, r.end_aci_cycles) for r in local.service.records], [(0, 1)])
        self.assertCountEqual(completions, [(0, 1), (1, 2), (2, 3), (3, 1)])
        self.assertTrue(owners.is_drained)
        owners.teardown()

    def test_client_network_wait_cannot_retain_memory_grant(self):
        env, owner = resource()
        handle = owner.reserve(buffer(ready=True))
        gate = env.event()
        def producer():
            lease = owner.try_acquire(handle, access())
            yield owner.try_service(lease, service_id="first", offset_bytes=0, size_bytes=32)
            owner.release_access(lease)
            yield gate  # Represents downstream backpressure after memory work.
        env.process(producer())
        other = owner.try_acquire(handle, access(offset=32))
        owner.try_service(other, service_id="other", offset_bytes=0, size_bytes=32)
        env.run()
        self.assertEqual(len(owner.service.records), 2)
        self.assertTrue(owner.service.is_drained)
        self.assertFalse(gate.triggered)
        owner.release_access(other)
        self.assertTrue(owner.is_drained)

    def test_service_rejects_unrepresentable_queued_finish_without_mutation(self):
        env = simpy.Environment(initial_time=1e308)
        service = MemoryService(env, "r", timing(granule=1, chunk=1, bandwidth=1, latency=5e307, native=1, aci=1))
        first = ServiceChunk(service_id="first", client_id="c", direction="read", address=0, useful_bytes=1)
        self.assertIsNotNone(service.try_submit(first))
        before = service.snapshot(), service.events
        with self.assertRaises(ValueError):
            service.try_submit(first.model_copy(update={"service_id": "overflow"}))
        self.assertEqual((service.snapshot(), service.events), before)


if __name__ == "__main__":
    unittest.main()
