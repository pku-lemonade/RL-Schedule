"""Strict opt-in scalar jobs for the canonical aggregate memory server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .configs.schemas.memory_replay import Positive
from .configs.schemas.topology import GraphRecord, Identifier, Index, PositiveInt
from .configs.schemas.torus_replay import Cycles


class CounterDefinition(GraphRecord):
    counter_id: Identifier
    address: Index
    width_bytes: PositiveInt
    granule_bytes: PositiveInt
    initial_value: Index


@dataclass(frozen=True, eq=False)
class CounterHandle:
    definition: CounterDefinition


class AtomicChunk(GraphRecord):
    service_id: Identifier
    client_id: Identifier
    counter_id: Identifier
    native_cycles: Positive
    direction: Literal["atomic"] = "atomic"


class ObservationChunk(GraphRecord):
    service_id: Identifier
    client_id: Identifier
    counter_id: Identifier
    control_aci_cycles: Cycles
    direction: Literal["observe"] = "observe"


class ScalarServiceRecord(GraphRecord):
    resource_id: Identifier
    service_id: Identifier
    client_id: Identifier
    counter_id: Identifier
    direction: Literal["atomic", "observe"]
    address: Index
    width_bytes: PositiveInt
    read_service_bytes: PositiveInt
    write_service_bytes: Index
    old_value: Index
    new_value: Index
    admission_sequence: Index
    linearization_sequence: Index | None
    queue_enter_aci_cycles: Cycles
    start_aci_cycles: Cycles
    end_aci_cycles: Cycles
    native_cycles: Positive
    service_aci_cycles: Positive


class CounterState(GraphRecord):
    definition: CounterDefinition
    value: Index
    updates: Index
