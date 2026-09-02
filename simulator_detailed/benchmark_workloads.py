"""Typed replays of the NMC schedules used by hardware microbenchmarks."""

from dataclasses import dataclass
from enum import Enum
from typing import cast

from simpy.events import Process, ProcessGenerator

from .pe_channel import NMCChannel, NMCReceiveResult, NMCTransmitResult
from .utils.definitions import Message, NMCShapeMode, NoCChannel


class NMCBenchmarkScenario(str, Enum):
    SEQUENTIAL_PING_PONG = "sequential_ping_pong"
    SINGLE_CHANNEL_BATCH = "single_channel_batch"
    DUAL_CHANNEL_SAME_DIRECTION_BATCH = "dual_channel_same_direction_batch"
    DUAL_CHANNEL_FULL_DUPLEX_BATCH = "dual_channel_full_duplex_batch"


@dataclass(frozen=True, slots=True)
class BatchedNMCStream:
    """One fixed-size, back-to-back message stream in a benchmark replay."""

    name: str
    source: NMCChannel
    destination: NMCChannel
    messages: tuple[Message, ...]
    receive_shape_mode: NMCShapeMode

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("benchmark stream name cannot be empty")
        if not self.messages:
            raise ValueError(f"benchmark stream {self.name} cannot be empty")
        if self.source.env is not self.destination.env:
            raise ValueError(
                f"benchmark stream {self.name} endpoints use different environments"
            )
        if self.source.fabric_id is not self.destination.fabric_id:
            raise ValueError(
                f"benchmark stream {self.name} crosses NoC fabrics"
            )
        payload_sizes = {message.payload_bytes() for message in self.messages}
        if len(payload_sizes) != 1:
            raise ValueError(
                f"benchmark stream {self.name} requires one fixed message size"
            )
        message_ids = [message.index for message in self.messages]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError(
                f"benchmark stream {self.name} has duplicate message IDs"
            )
        for message in self.messages:
            if message.src != self.source.binding.address:
                raise ValueError(
                    f"benchmark stream {self.name} has a mismatched source"
                )
            if message.dst != self.destination.binding.address:
                raise ValueError(
                    f"benchmark stream {self.name} has a mismatched destination"
                )

    @property
    def payload_bytes(self) -> int:
        return sum(message.payload_bytes() for message in self.messages)


@dataclass(frozen=True, slots=True)
class BatchedNMCStreamResult:
    """Endpoint operation results for one replayed message stream."""

    stream: BatchedNMCStream
    sends: tuple[NMCTransmitResult, ...]
    receives: tuple[NMCReceiveResult, ...]

    def __post_init__(self) -> None:
        expected_count = len(self.stream.messages)
        if (
            len(self.sends) != expected_count
            or len(self.receives) != expected_count
        ):
            raise ValueError(
                f"benchmark stream {self.stream.name} result count mismatch"
            )


@dataclass(frozen=True, slots=True)
class BatchedNMCReplayResult:
    """Wall-clock result for one explicitly named batched command schedule."""

    scenario: NMCBenchmarkScenario
    streams: tuple[BatchedNMCStreamResult, ...]
    operation_start_time_aci_cycles: float
    operation_completion_time_aci_cycles: float

    @property
    def operation_latency_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.operation_start_time_aci_cycles
        )

    @property
    def aggregate_payload_bytes(self) -> int:
        return sum(result.stream.payload_bytes for result in self.streams)

    @property
    def aggregate_throughput_bytes_per_aci_cycle(self) -> float:
        latency = self.operation_latency_aci_cycles
        if latency <= 0:
            raise ValueError("benchmark replay latency must be positive")
        return self.aggregate_payload_bytes / latency

    def stream_throughput_bytes_per_aci_cycle(self, name: str) -> float:
        matching = [result for result in self.streams if result.stream.name == name]
        if len(matching) != 1:
            raise KeyError(f"benchmark replay has no unique stream named {name!r}")
        return matching[0].stream.payload_bytes / self.operation_latency_aci_cycles


@dataclass(frozen=True, slots=True)
class SequentialPingPongResult:
    """Endpoint results for a forward transfer followed by its reverse reply."""

    forward_send: NMCTransmitResult
    forward_receive: NMCReceiveResult
    reverse_send: NMCTransmitResult
    reverse_receive: NMCReceiveResult

    @property
    def scenario(self) -> NMCBenchmarkScenario:
        return NMCBenchmarkScenario.SEQUENTIAL_PING_PONG

    @property
    def operation_start_time_aci_cycles(self) -> float:
        return self.forward_send.submission_time_aci_cycles

    @property
    def operation_completion_time_aci_cycles(self) -> float:
        return self.reverse_receive.operation_completion_time_aci_cycles

    @property
    def operation_rtt_aci_cycles(self) -> float:
        return (
            self.operation_completion_time_aci_cycles
            - self.operation_start_time_aci_cycles
        )

    @property
    def forward_operation_latency_aci_cycles(self) -> float:
        return (
            self.forward_receive.operation_completion_time_aci_cycles
            - self.forward_send.submission_time_aci_cycles
        )

    @property
    def reverse_operation_latency_aci_cycles(self) -> float:
        return (
            self.reverse_receive.operation_completion_time_aci_cycles
            - self.reverse_send.submission_time_aci_cycles
        )


def replay_sequential_ping_pong(
    initiator: NMCChannel,
    responder: NMCChannel,
    forward_message: Message,
    reverse_message: Message,
    *,
    forward_receive_shape_mode: NMCShapeMode,
    reverse_receive_shape_mode: NMCShapeMode,
) -> Process:
    """Replay forward completion followed by a reverse reply on one fabric."""
    _validate_ping_pong(
        initiator,
        responder,
        forward_message,
        reverse_message,
    )
    return initiator.env.process(
        _run_sequential_ping_pong(
            initiator,
            responder,
            forward_message,
            reverse_message,
            forward_receive_shape_mode,
            reverse_receive_shape_mode,
        )
    )


def replay_single_channel_batch(stream: BatchedNMCStream) -> Process:
    """Replay one fixed-size stream and one final completion boundary."""
    return stream.source.env.process(
        _run_batched_streams(
            NMCBenchmarkScenario.SINGLE_CHANNEL_BATCH,
            (stream,),
        )
    )


def replay_dual_channel_same_direction_batch(
    ch0_stream: BatchedNMCStream,
    ch1_stream: BatchedNMCStream,
) -> Process:
    """Replay matching same-direction streams on both independent fabrics."""
    streams = (ch0_stream, ch1_stream)
    _validate_dual_same_direction(streams)
    return ch0_stream.source.env.process(
        _run_batched_streams(
            NMCBenchmarkScenario.DUAL_CHANNEL_SAME_DIRECTION_BATCH,
            streams,
        )
    )


def replay_dual_channel_full_duplex_batch(
    ch0_forward: BatchedNMCStream,
    ch0_reverse: BatchedNMCStream,
    ch1_forward: BatchedNMCStream,
    ch1_reverse: BatchedNMCStream,
) -> Process:
    """Replay two directions concurrently on both independent fabrics."""
    streams = (ch0_forward, ch0_reverse, ch1_forward, ch1_reverse)
    _validate_dual_full_duplex(streams)
    return ch0_forward.source.env.process(
        _run_batched_streams(
            NMCBenchmarkScenario.DUAL_CHANNEL_FULL_DUPLEX_BATCH,
            streams,
        )
    )


def _run_sequential_ping_pong(
    initiator: NMCChannel,
    responder: NMCChannel,
    forward_message: Message,
    reverse_message: Message,
    forward_receive_shape_mode: NMCShapeMode,
    reverse_receive_shape_mode: NMCShapeMode,
) -> ProcessGenerator:
    # The initiator posts SEND before its reply RECV; the responder replies only
    # after the forward command has completed.
    forward_send_process = initiator.send(forward_message)
    reverse_receive_process = initiator.recv_message(
        reverse_message,
        reverse_receive_shape_mode,
    )
    forward_receive_process = responder.recv_message(
        forward_message,
        forward_receive_shape_mode,
    )
    forward_receive = cast(
        NMCReceiveResult,
        (yield forward_receive_process),
    )
    reverse_send_process = responder.send(reverse_message)
    yield initiator.env.all_of(
        (
            forward_send_process,
            reverse_send_process,
            reverse_receive_process,
        )
    )
    return SequentialPingPongResult(
        forward_send=cast(NMCTransmitResult, forward_send_process.value),
        forward_receive=forward_receive,
        reverse_send=cast(NMCTransmitResult, reverse_send_process.value),
        reverse_receive=cast(NMCReceiveResult, reverse_receive_process.value),
    )


def _run_batched_streams(
    scenario: NMCBenchmarkScenario,
    streams: tuple[BatchedNMCStream, ...],
) -> ProcessGenerator:
    _validate_common_streams(streams)
    env = streams[0].source.env

    # Hardware batches outgoing descriptors before one final fence. Starting
    # SEND commands first also makes same-channel full-duplex posting order
    # explicit; matching RECV commands are posted at the same simulation time.
    send_processes = tuple(
        tuple(stream.source.send(message) for message in stream.messages)
        for stream in streams
    )
    receive_processes = tuple(
        tuple(
            stream.destination.recv_message(
                message,
                stream.receive_shape_mode,
            )
            for message in stream.messages
        )
        for stream in streams
    )
    yield env.all_of(
        tuple(
            process
            for process_group in (*send_processes, *receive_processes)
            for process in process_group
        )
    )

    stream_results = tuple(
        BatchedNMCStreamResult(
            stream=stream,
            sends=tuple(
                cast(NMCTransmitResult, process.value)
                for process in send_processes[index]
            ),
            receives=tuple(
                cast(NMCReceiveResult, process.value)
                for process in receive_processes[index]
            ),
        )
        for index, stream in enumerate(streams)
    )
    operation_start_time = min(
        result.submission_time_aci_cycles
        for stream_result in stream_results
        for result in (*stream_result.sends, *stream_result.receives)
    )
    operation_completion_time = max(
        result.operation_completion_time_aci_cycles
        for stream_result in stream_results
        for result in (*stream_result.sends, *stream_result.receives)
    )
    return BatchedNMCReplayResult(
        scenario=scenario,
        streams=stream_results,
        operation_start_time_aci_cycles=operation_start_time,
        operation_completion_time_aci_cycles=operation_completion_time,
    )


def _validate_ping_pong(
    initiator: NMCChannel,
    responder: NMCChannel,
    forward_message: Message,
    reverse_message: Message,
) -> None:
    if initiator.env is not responder.env:
        raise ValueError("ping-pong endpoints use different environments")
    if initiator.fabric_id is not responder.fabric_id:
        raise ValueError("ping-pong endpoints use different NoC fabrics")
    if forward_message.index == reverse_message.index:
        raise ValueError("ping-pong directions require distinct message IDs")
    if (
        forward_message.src != initiator.binding.address
        or forward_message.dst != responder.binding.address
        or reverse_message.src != responder.binding.address
        or reverse_message.dst != initiator.binding.address
    ):
        raise ValueError("ping-pong messages do not match the channel endpoints")


def _validate_common_streams(streams: tuple[BatchedNMCStream, ...]) -> None:
    if not streams:
        raise ValueError("benchmark replay requires at least one stream")
    env = streams[0].source.env
    if any(stream.source.env is not env for stream in streams):
        raise ValueError("benchmark streams use different environments")
    names = [stream.name for stream in streams]
    if len(names) != len(set(names)):
        raise ValueError("benchmark stream names must be unique")
    message_keys = [
        (stream.source.fabric_id, message.index)
        for stream in streams
        for message in stream.messages
    ]
    if len(message_keys) != len(set(message_keys)):
        raise ValueError("benchmark messages must be unique within each fabric")
    counts = {len(stream.messages) for stream in streams}
    sizes = {stream.messages[0].payload_bytes() for stream in streams}
    if len(counts) != 1 or len(sizes) != 1:
        raise ValueError("parallel benchmark streams must use one batch shape")


def _validate_dual_same_direction(
    streams: tuple[BatchedNMCStream, BatchedNMCStream],
) -> None:
    _validate_common_streams(streams)
    if {stream.source.fabric_id for stream in streams} != set(NoCChannel):
        raise ValueError("dual-channel replay requires one stream per fabric")
    endpoint_pairs = {
        (
            stream.source.binding.address.node_id,
            stream.destination.binding.address.node_id,
        )
        for stream in streams
    }
    if len(endpoint_pairs) != 1:
        raise ValueError("dual-channel streams must use the same direction")


def _validate_dual_full_duplex(
    streams: tuple[
        BatchedNMCStream,
        BatchedNMCStream,
        BatchedNMCStream,
        BatchedNMCStream,
    ],
) -> None:
    _validate_common_streams(streams)
    for fabric_id in NoCChannel:
        fabric_streams = tuple(
            stream for stream in streams if stream.source.fabric_id is fabric_id
        )
        if len(fabric_streams) != 2:
            raise ValueError(
                "dual-channel full-duplex replay requires two streams per fabric"
            )
        first, second = fabric_streams
        if (
            first.source.binding.address.node_id
            != second.destination.binding.address.node_id
            or first.destination.binding.address.node_id
            != second.source.binding.address.node_id
        ):
            raise ValueError(
                f"{fabric_id.name} full-duplex streams are not opposite directions"
            )
