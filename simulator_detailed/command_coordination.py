"""Runtime coordination for paired DMA commands."""

from __future__ import annotations

from dataclasses import dataclass

import simpy
from simpy.events import Event as SimpyEvent

from .utils.definitions import Message, NoCChannel, NodeType


@dataclass(frozen=True, slots=True)
class PairedCommandKey:
    """Identity of one dual-side PE-to-DMA command pair."""

    fabric_id: NoCChannel
    message_id: int
    source_type: NodeType
    source_id: int
    destination_type: NodeType
    destination_id: int

    @classmethod
    def from_message(cls, message: Message) -> PairedCommandKey:
        return cls(
            fabric_id=message.src.fabric_id,
            message_id=message.index,
            source_type=message.src.node_type,
            source_id=message.src.node_id,
            destination_type=message.dst.node_type,
            destination_id=message.dst.node_id,
        )


class PairedDMACommandCoordinator:
    """Match dual-side source payload with destination descriptor admission."""

    def __init__(self, env: simpy.Environment) -> None:
        self.env = env
        self._destination_ready: dict[PairedCommandKey, SimpyEvent] = {}

    def destination_ready_event(self, message: Message) -> SimpyEvent:
        key = PairedCommandKey.from_message(message)
        event = self._destination_ready.get(key)
        if event is None:
            event = self.env.event()
            self._destination_ready[key] = event
        return event

    def admit_destination(self, message: Message) -> None:
        event = self.destination_ready_event(message)
        if event.triggered:
            raise RuntimeError(
                f"message {message.index} destination was admitted more than once"
            )
        event.succeed()

    def retire(self, message: Message, event: SimpyEvent) -> None:
        key = PairedCommandKey.from_message(message)
        if self._destination_ready.get(key) is not event:
            raise RuntimeError(
                f"message {message.index} has no matching destination admission"
            )
        if not event.triggered:
            raise RuntimeError(
                f"message {message.index} destination is not admitted"
            )
        del self._destination_ready[key]


__all__ = [
    "PairedCommandKey",
    "PairedDMACommandCoordinator",
]
