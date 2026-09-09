"""Runtime protocol state for commands involving DMA endpoints."""

from __future__ import annotations

from dataclasses import dataclass

import simpy
from simpy.events import Event as SimpyEvent

from .utils.definitions import (
    DMA_RDMA_NODE_TYPES,
    DMACommandMode,
    EndpointAddress,
    Flit,
    FlitTrafficType,
    FlitType,
    Message,
    NoCChannel,
    NodeType,
)


@dataclass(frozen=True, slots=True)
class DMACommandKey:
    """Full wire identity of one dual-side DMA command pair."""

    fabric_id: NoCChannel
    message_id: int
    source_type: NodeType
    source_id: int
    destination_type: NodeType
    destination_id: int

    @classmethod
    def from_message(cls, message: Message) -> DMACommandKey:
        return cls(
            fabric_id=message.src.fabric_id,
            message_id=message.index,
            source_type=message.src.node_type,
            source_id=message.src.node_id,
            destination_type=message.dst.node_type,
            destination_id=message.dst.node_id,
        )


@dataclass(frozen=True, slots=True)
class SingleSideCommandKey:
    """Task-ID identity carried by one single-side request/response exchange."""

    fabric_id: NoCChannel
    task_id: int

    @classmethod
    def from_message(cls, message: Message) -> SingleSideCommandKey:
        return cls(message.src.fabric_id, message.index)

    @classmethod
    def from_flit(cls, flit: Flit) -> SingleSideCommandKey:
        return cls(flit.fabric_id, flit.msg_id)


@dataclass(slots=True)
class DualSideCommandState:
    """Software pairing and outer-sync admission state for one command."""

    payload_ready: SimpyEvent
    source_posted: bool = False
    destination_posted: bool = False
    source_completed: bool = False
    destination_completed: bool = False


@dataclass(slots=True)
class SingleSideDownloadState:
    """One PE-initiated read request waiting for an RDMA response."""

    message: Message
    request_flit: Flit
    request_accepted: bool = False


@dataclass(slots=True)
class SingleSideUploadState:
    """One PE-initiated write request waiting for a GM_WDMA response."""

    message: Message
    completion: SimpyEvent
    target_accepted: bool = False
    response_created: bool = False


class DMACommandCoordinator:
    """Own dual-side pairing and single-side task-ID matching state."""

    def __init__(self, env: simpy.Environment) -> None:
        self.env = env
        self._dual_side: dict[DMACommandKey, DualSideCommandState] = {}
        self._single_side_downloads: dict[
            SingleSideCommandKey, SingleSideDownloadState
        ] = {}
        self._single_side_uploads: dict[
            SingleSideCommandKey, SingleSideUploadState
        ] = {}

    @property
    def pending_dual_side_commands(self) -> int:
        return len(self._dual_side)

    @property
    def pending_single_side_commands(self) -> int:
        return len(self._single_side_downloads) + len(self._single_side_uploads)

    def post_dual_side_source(self, message: Message) -> SimpyEvent:
        """Post the source descriptor and return its outer-sync admission event."""
        self._require_mode(message, DMACommandMode.DUAL_SIDE)
        key = DMACommandKey.from_message(message)
        state = self._dual_side_state(key)
        if state.source_posted:
            raise RuntimeError(
                f"message {message.index} dual-side source was posted more than once"
            )
        state.source_posted = True
        self._release_dual_side_payload(state)
        return state.payload_ready

    def post_dual_side_destination(self, message: Message) -> None:
        """Post the destination descriptor and release a matched source."""
        self._require_mode(message, DMACommandMode.DUAL_SIDE)
        key = DMACommandKey.from_message(message)
        state = self._dual_side_state(key)
        if state.destination_posted:
            raise RuntimeError(
                f"message {message.index} dual-side destination was posted more than once"
            )
        state.destination_posted = True
        self._release_dual_side_payload(state)

    def complete_dual_side_source(
        self,
        message: Message,
        payload_ready: SimpyEvent,
    ) -> None:
        """Record the source's final payload handoff."""
        key = DMACommandKey.from_message(message)
        state = self._dual_side.get(key)
        if state is None or state.payload_ready is not payload_ready:
            raise RuntimeError(
                f"message {message.index} has no matching dual-side command"
            )
        if not (
            state.source_posted
            and state.destination_posted
            and state.payload_ready.triggered
        ):
            raise RuntimeError(f"message {message.index} dual-side pair is incomplete")
        if state.source_completed:
            raise RuntimeError(
                f"message {message.index} dual-side source completed twice"
            )
        state.source_completed = True
        self._retire_completed_dual_side(key, state)

    def complete_dual_side_destination(self, message: Message) -> None:
        """Record destination completion and retire a fully completed pair."""
        key = DMACommandKey.from_message(message)
        state = self._dual_side.get(key)
        if state is None or not state.payload_ready.triggered:
            raise RuntimeError(
                f"message {message.index} has no admitted dual-side command"
            )
        if state.destination_completed:
            raise RuntimeError(
                f"message {message.index} dual-side destination completed twice"
            )
        state.destination_completed = True
        self._retire_completed_dual_side(key, state)

    def post_single_side_download(self, message: Message) -> Flit:
        """Register a PE read command and build its request header flit."""
        self._require_mode(message, DMACommandMode.SINGLE_SIDE)
        if (
            message.src.node_type not in DMA_RDMA_NODE_TYPES
            or message.dst.node_type is not NodeType.PE
        ):
            raise ValueError(
                f"message {message.index} is not a supported single-side direction"
            )
        key = SingleSideCommandKey.from_message(message)
        self._require_unused_single_side_key(key)
        request_flit = self._control_flit(
            message,
            FlitTrafficType.DMA_REQUEST,
        )
        self._single_side_downloads[key] = SingleSideDownloadState(
            message=message,
            request_flit=request_flit,
        )
        return request_flit

    def accept_single_side_download_request(
        self,
        request_flit: Flit,
        responder: EndpointAddress,
    ) -> Message:
        """Match a request header at its RDMA by fabric and task ID."""
        key = SingleSideCommandKey.from_flit(request_flit)
        state = self._single_side_downloads.get(key)
        if state is None:
            raise RuntimeError(
                f"task {request_flit.msg_id} has no pending single-side download"
            )
        if responder != state.message.src or request_flit != state.request_flit:
            raise RuntimeError(
                f"task {request_flit.msg_id} has an invalid single-side download request"
            )
        if state.request_accepted:
            raise RuntimeError(
                f"task {request_flit.msg_id} download request was accepted twice"
            )
        state.request_accepted = True
        return state.message

    def complete_single_side_download(self, message: Message) -> None:
        """Retire a download after the initiator receives the payload response."""
        key = SingleSideCommandKey.from_message(message)
        state = self._single_side_downloads.get(key)
        if state is None or state.message != message or not state.request_accepted:
            raise RuntimeError(
                f"message {message.index} has no accepted single-side download"
            )
        del self._single_side_downloads[key]

    def post_single_side_upload(self, message: Message) -> SimpyEvent:
        """Register a PE write command and return its response event."""
        self._require_single_side_direction(
            message,
            source_type=NodeType.PE,
            destination_type=NodeType.GM_WDMA,
        )
        key = SingleSideCommandKey.from_message(message)
        self._require_unused_single_side_key(key)
        completion = self.env.event()
        self._single_side_uploads[key] = SingleSideUploadState(
            message=message,
            completion=completion,
        )
        return completion

    def accept_single_side_upload_header(
        self,
        first_flit: Flit,
        responder: EndpointAddress,
    ) -> Message:
        """Match an address-bearing upload HEAD at GM_WDMA."""
        key = SingleSideCommandKey.from_flit(first_flit)
        state = self._single_side_uploads.get(key)
        if state is None:
            raise RuntimeError(
                f"task {first_flit.msg_id} has no pending single-side upload"
            )
        if responder != state.message.dst:
            raise RuntimeError(
                f"task {first_flit.msg_id} reached the wrong single-side target"
            )
        state.message.validate_flit(first_flit, 0, state.message.flit_count())
        if first_flit.dma_header_bytes != state.message.header_bytes:
            raise RuntimeError(
                f"task {first_flit.msg_id} upload has no address header"
            )
        if state.target_accepted:
            raise RuntimeError(
                f"task {first_flit.msg_id} upload header was accepted twice"
            )
        state.target_accepted = True
        return state.message

    def create_single_side_upload_response(self, message: Message) -> Flit:
        """Build the GM_WDMA completion response after target-side service."""
        key = SingleSideCommandKey.from_message(message)
        state = self._single_side_uploads.get(key)
        if state is None or state.message != message or not state.target_accepted:
            raise RuntimeError(
                f"message {message.index} has no accepted single-side upload"
            )
        if state.response_created:
            raise RuntimeError(
                f"message {message.index} single-side response was created twice"
            )
        state.response_created = True
        return self._control_flit(message, FlitTrafficType.DMA_RESPONSE)

    def accept_single_side_upload_response(
        self,
        response_flit: Flit,
        initiator: EndpointAddress,
    ) -> None:
        """Match the completion response at the initiating PE."""
        key = SingleSideCommandKey.from_flit(response_flit)
        state = self._single_side_uploads.get(key)
        if state is None:
            raise RuntimeError(
                f"task {response_flit.msg_id} has no pending single-side upload"
            )
        expected_response = self._control_flit(
            state.message,
            FlitTrafficType.DMA_RESPONSE,
        )
        if initiator != state.message.src or response_flit != expected_response:
            raise RuntimeError(
                f"task {response_flit.msg_id} has an invalid single-side response"
            )
        if not state.response_created:
            raise RuntimeError(
                f"task {response_flit.msg_id} response arrived before completion"
            )
        state.completion.succeed()
        del self._single_side_uploads[key]

    def _dual_side_state(self, key: DMACommandKey) -> DualSideCommandState:
        state = self._dual_side.get(key)
        if state is None:
            state = DualSideCommandState(payload_ready=self.env.event())
            self._dual_side[key] = state
        return state

    @staticmethod
    def _release_dual_side_payload(state: DualSideCommandState) -> None:
        if (
            state.source_posted
            and state.destination_posted
            and not state.payload_ready.triggered
        ):
            state.payload_ready.succeed()

    def _retire_completed_dual_side(
        self,
        key: DMACommandKey,
        state: DualSideCommandState,
    ) -> None:
        if state.source_completed and state.destination_completed:
            del self._dual_side[key]

    def _require_unused_single_side_key(
        self,
        key: SingleSideCommandKey,
    ) -> None:
        if (
            key in self._single_side_downloads
            or key in self._single_side_uploads
        ):
            raise RuntimeError(
                f"task {key.task_id} is already active on {key.fabric_id.name}"
            )

    @staticmethod
    def _require_mode(message: Message, mode: DMACommandMode) -> None:
        if message.dma_command_mode is not mode:
            raise ValueError(
                f"message {message.index} requires {mode.name} command mode"
            )

    @classmethod
    def _require_single_side_direction(
        cls,
        message: Message,
        *,
        source_type: NodeType,
        destination_type: NodeType,
    ) -> None:
        cls._require_mode(message, DMACommandMode.SINGLE_SIDE)
        if (
            message.src.node_type is not source_type
            or message.dst.node_type is not destination_type
        ):
            raise ValueError(
                f"message {message.index} is not a supported single-side direction"
            )

    @staticmethod
    def _control_flit(
        message: Message,
        traffic_type: FlitTrafficType,
    ) -> Flit:
        return Flit(
            flit_type=FlitType.SINGLE,
            payload_bytes=0,
            msg_id=message.index,
            fabric_id=message.src.fabric_id,
            dst_router=message.src.router_id,
            dst_local_port=message.src.local_port,
            src_router=message.dst.router_id,
            src_local_port=message.dst.local_port,
            burst_len_mode=message.burst_len_mode,
            traffic_type=traffic_type,
            dma_header_bytes=message.header_bytes,
        )


__all__ = [
    "DMACommandCoordinator",
    "DMACommandKey",
    "DualSideCommandState",
    "SingleSideCommandKey",
    "SingleSideDownloadState",
    "SingleSideUploadState",
]
