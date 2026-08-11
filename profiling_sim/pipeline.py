"""Pipeline auto-sync (inner sync) buffer-hazard scoreboard.

Models the ``cute::ada::Pipeline`` template described in NOC_ARCHITECTURE.md
§7.4: buffers are keyed by ``(buf_id, chunk)`` and RAW / WAW / WAR hazards
are resolved with zero-cost :class:`simpy.Event` dependencies.  The primitive
only tracks buffer readiness; per-unit occupancy / initiation interval is
the responsibility of :class:`profiling_sim.shadow.ShadowPipeline`.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

import simpy


class Access(Enum):
    READ = "READ"
    WRITE = "WRITE"


class BufferSlot:
    """A handle to an in-flight pipeline access.

    Call :meth:`release` (idempotent, safe in ``try/finally``) once the
    caller's work is done to publish the buffer-ready event to waiters.
    """

    __slots__ = (
        "_pipeline", "buf_id", "chunk", "access",
        "register_time", "_released", "_release_event", "_batch",
    )

    def __init__(self, pipeline: "Pipeline", buf_id: Any, chunk: int,
                 access: Access, register_time: float,
                 release_event: Optional[simpy.Event] = None,
                 batch: Optional[List] = None) -> None:
        self._pipeline = pipeline
        self.buf_id = buf_id
        self.chunk = chunk
        self.access = access
        self.register_time = register_time
        self._released = False
        self._release_event = release_event
        self._batch = batch

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._pipeline._release(self)


class _BufferState:
    __slots__ = ("write_event", "read_batch")

    def __init__(self, env: simpy.Environment) -> None:
        self.write_event: simpy.Event = env.event()
        self.write_event.succeed()
        self.read_batch: Optional[List] = None


class Pipeline:
    """Buffer-readiness scoreboard with auto/consume and manual modes."""

    def __init__(self, env: simpy.Environment, auto_consume: bool = True,
                 name: str = "pipeline") -> None:
        self.env = env
        self.auto_consume = auto_consume
        self.name = name
        self._states: Dict[Any, _BufferState] = {}
        self.events: List[Dict[str, Any]] = []

    def _validate_chunk(self, chunk: int) -> None:
        if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk < 0:
            raise ValueError(
                f"chunk must be a non-negative int, got {chunk!r}")

    def _key(self, buf_id: Any, chunk: int) -> Any:
        self._validate_chunk(chunk)
        return (buf_id, chunk)

    def _state(self, key: Any) -> _BufferState:
        st = self._states.get(key)
        if st is None:
            st = _BufferState(self.env)
            self._states[key] = st
        return st

    def _log(self, buf_id: Any, chunk: int, access: Access, phase: str) -> None:
        self.events.append({
            "time": self.env.now,
            "buf_id": buf_id,
            "chunk": chunk,
            "access": access,
            "phase": phase,
        })

    def write(self, buf_id: Any, chunk: int):
        """Register (and in auto mode wait for) a write access."""
        key = self._key(buf_id, chunk)
        st = self._state(key)

        prev_write = st.write_event
        prev_batch = st.read_batch
        my_release = self.env.event()
        st.write_event = my_release
        st.read_batch = None

        slot = BufferSlot(self, buf_id, chunk, Access.WRITE, self.env.now,
                          release_event=my_release)
        self._log(buf_id, chunk, Access.WRITE, "register")

        if self.auto_consume:
            yield prev_write
            if prev_batch is not None:
                yield prev_batch[0]
            self._log(buf_id, chunk, Access.WRITE, "acquire")
        return slot

    def read(self, buf_id: Any, chunk: int):
        """Register (and in auto mode wait for) a read access."""
        key = self._key(buf_id, chunk)
        st = self._state(key)

        prev_write = st.write_event
        if st.read_batch is None or st.read_batch[1] == 0:
            st.read_batch = [self.env.event(), 0]
        batch = st.read_batch
        batch[1] += 1

        slot = BufferSlot(self, buf_id, chunk, Access.READ, self.env.now,
                          batch=batch)
        self._log(buf_id, chunk, Access.READ, "register")

        if self.auto_consume:
            yield prev_write
            self._log(buf_id, chunk, Access.READ, "acquire")
        return slot

    produce = write
    consume = read

    def wait(self, buf_id: Any, chunk: int, access):
        """Explicit snapshot barrier (manual mode).

        Captures the current scoreboard state and yields until the hazard for
        ``access`` clears.  Does not register an access.  Logs an ``acquire``
        event on return.
        """
        if not isinstance(access, Access):
            try:
                access = Access(access)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"access must be Access.READ or Access.WRITE, got "
                    f"{access!r}") from exc
        key = self._key(buf_id, chunk)
        st = self._state(key)
        if access == Access.WRITE:
            yield st.write_event
            if st.read_batch is not None:
                yield st.read_batch[0]
        else:
            yield st.write_event
        self._log(buf_id, chunk, access, "acquire")

    def _release(self, slot: BufferSlot) -> None:
        self._log(slot.buf_id, slot.chunk, slot.access, "release")
        if slot.access == Access.WRITE:
            slot._release_event.succeed()
        else:
            batch = slot._batch
            batch[1] -= 1
            if batch[1] == 0:
                batch[0].succeed()
