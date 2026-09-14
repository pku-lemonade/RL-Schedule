from enum import Enum
from typing import Any, Dict, List, Optional

import simpy


class ShadowEntry(str, Enum):
    ST_MDMA_LOCAL_MEMORY_DOWNLOAD = "dma.local_memory"
    ST_ACI_FUNC = "interchip.transfer"
    ST_ACI_LOCAL_MEMORY_DOWNLOAD = "interchip.local_memory"


def sramc_entry(channel: int, upload: bool) -> str:
    if channel < 0:
        raise ValueError("channel must be nonnegative")
    return f"sram.{channel}.{'upload' if upload else 'download'}"


def mdma_channel_entry(channel: int) -> str:
    if channel < 0:
        raise ValueError("channel must be nonnegative")
    return f"dma.channel.{channel}"


class ShadowPipeline:
    """Cycle-accurate shadow pipeline primitive.

    `slots` (a Container) bounds the number of in-flight operations
    (configured occupancy).  `admit` (a Resource of capacity 1)
    together with ``_next_admit`` enforces the initiation interval ``ii``.

    Two access modes are provided:

    * :meth:`enter` runs a self-contained traversal of all stages (used by PE
      compute where the pipeline *is* the critical section).
    * :meth:`acquire` / :meth:`release` wrap a caller-owned critical section
      (MDMA engine time / InterChip fixed latency): the slot is held across the
      caller's body.
    """

    def __init__(
        self,
        env: simpy.Environment,
        name: str,
        entry_ids: List[str | int],
        stage_latencies: List[int],
        occupancy: int = 1,
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

    def _latencies(self, stage_overrides: Optional[Dict[str | int, int]]):
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

    def enter(
        self, tag: Any = None, stage_overrides: Optional[Dict[str | int, int]] = None
    ):
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
                        "entry_id": entry_id,
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
            "entry_id": self.entry_ids[0],
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
