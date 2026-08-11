from enum import IntEnum
from typing import Any, Dict, List, Optional

import simpy


class ShadowEntry(IntEnum):
    ST_PE_MATRIX_READ_F = 4
    ST_PE_MATRIX_READ_W = 5
    ST_PE_MATRIX_CAL = 6
    ST_PE_MATRIX_WRITE = 7

    ST_PE_VECTOR_READ = 8
    ST_PE_VECTOR_CAL = 9
    ST_PE_VECTOR_WRITE = 10

    ST_SRAMC_DNLD_0 = 12
    ST_SRAMC_UPLD_0 = 16
    ST_SRAMC_DNLD_1 = 20
    ST_SRAMC_UPLD_1 = 24

    ST_MDMA_START = 32
    ST_MDMA_CHANNEL_0 = 36
    ST_MDMA_CHANNEL_1 = 40
    ST_MDMA_AIU_DOWNLOAD = 44

    ST_ACI_START = 64
    ST_ACI_FUNC = 68
    ST_ACI_AIU_DOWNLOAD = 92


def sramc_entry(channel: int, upload: bool) -> ShadowEntry:
    if channel == 0:
        return ShadowEntry.ST_SRAMC_UPLD_0 if upload else ShadowEntry.ST_SRAMC_DNLD_0
    if channel == 1:
        return ShadowEntry.ST_SRAMC_UPLD_1 if upload else ShadowEntry.ST_SRAMC_DNLD_1
    raise ValueError(f"invalid SRAMC channel {channel}")


def mdma_channel_entry(channel: int) -> ShadowEntry:
    if channel == 0:
        return ShadowEntry.ST_MDMA_CHANNEL_0
    if channel == 1:
        return ShadowEntry.ST_MDMA_CHANNEL_1
    raise ValueError(f"invalid MDMA channel {channel}")


class ShadowPipeline:
    """Cycle-accurate shadow pipeline primitive.

    `slots` (a Container) bounds the number of in-flight operations
    (occupancy = the 4-entry spacing).  `admit` (a Resource of capacity 1)
    together with ``_next_admit`` enforces the initiation interval ``ii``.

    Two access modes are provided:

    * :meth:`enter` runs a self-contained traversal of all stages (used by PE
      compute where the pipeline *is* the critical section).
    * :meth:`acquire` / :meth:`release` wrap a caller-owned critical section
      (MDMA engine time / AdaLink fixed latency): the slot is held across the
      caller's body.
    """

    def __init__(
        self,
        env: simpy.Environment,
        name: str,
        entry_ids: List[int],
        stage_latencies: List[int],
        occupancy: int = 4,
        ii: int = 1,
    ) -> None:
        if len(entry_ids) != len(stage_latencies):
            raise ValueError("entry_ids and stage_latencies length mismatch")
        if not entry_ids:
            raise ValueError("pipeline must have at least one stage")
        if occupancy < 1 or ii < 1:
            raise ValueError("occupancy and ii must be >= 1")
        for lat in stage_latencies:
            if lat < 0:
                raise ValueError("stage latency must be >= 0")
        self.env = env
        self.name = name
        self.entry_ids = list(entry_ids)
        self.stage_latencies = list(stage_latencies)
        self.occupancy = occupancy
        self.ii = ii
        self.slots = simpy.Container(env, capacity=occupancy, init=occupancy)
        self.admit = simpy.Resource(env, capacity=1)
        self._next_admit = 0
        self.events: List[Dict[str, Any]] = []
        self.occupancy_log: List[tuple] = []

    @property
    def latency(self) -> int:
        return sum(self.stage_latencies)

    def _latencies(self, stage_overrides: Optional[Dict[int, int]]):
        lats = list(self.stage_latencies)
        if stage_overrides:
            ids = list(self.entry_ids)
            for key, val in stage_overrides.items():
                if val < 0:
                    raise ValueError("override latency must be >= 0")
                try:
                    idx = ids.index(key)
                except ValueError as exc:
                    raise ValueError(
                        f"stage_overrides entry id {key} not in pipeline {self.name}"
                    ) from exc
                lats[idx] = val
        return lats

    def _log_occupancy(self) -> None:
        self.occupancy_log.append((self.env.now, self.occupancy - self.slots.level))

    def _admit(self) -> simpy.Event:
        req = self.admit.request()
        yield req
        try:
            wait = self._next_admit - self.env.now
            if wait > 0:
                yield self.env.timeout(wait)
            self._next_admit = self.env.now + self.ii
        finally:
            self.admit.release(req)

    def enter(self, tag: Any = None, stage_overrides: Optional[Dict[int, int]] = None):
        lats = self._latencies(stage_overrides)
        yield self.slots.get(1)
        self._log_occupancy()
        try:
            yield self.env.process(self._admit())
            for entry_id, lat in zip(self.entry_ids, lats):
                enter = self.env.now
                if lat:
                    yield self.env.timeout(lat)
                self.events.append(
                    {
                        "entry_id": int(entry_id),
                        "enter": enter,
                        "exit": self.env.now,
                        "tag": tag,
                    }
                )
        finally:
            yield self.slots.put(1)
            self._log_occupancy()

    def acquire(self, tag: Any = None):
        yield self.slots.get(1)
        self._log_occupancy()
        yield self.env.process(self._admit())
        enter = self.env.now
        lat = self.stage_latencies[0]
        if lat:
            yield self.env.timeout(lat)
        return {
            "entry_id": int(self.entry_ids[0]),
            "enter": enter,
            "tag": tag,
        }

    def release(self, slot: Dict[str, Any]):
        self.events.append(
            {
                "entry_id": slot["entry_id"],
                "enter": slot["enter"],
                "exit": self.env.now,
                "tag": slot["tag"],
            }
        )
        yield self.slots.put(1)
        self._log_occupancy()
