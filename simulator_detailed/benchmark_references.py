"""Immutable hardware measurements used to validate, not configure, Phase 2."""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class RTTBenchmarkReference:
    """Measured latency evidence from one hardware microbenchmark."""

    kernel: str
    flit_bytes: int
    default_burst_flits: int
    hidden_serialization_through_bytes: int
    rtt_intercept_aci_cycles: float
    rtt_hop_slope_aci_cycles: float
    payload_samples: tuple[tuple[int, float, int], ...]
    hop_samples: tuple[tuple[int, float], ...]

    def linear_rtt_aci_cycles(self, hops: int) -> float:
        """Return the measured single-flit RTT trend for a positive hop count."""
        if hops < 1:
            raise ValueError("benchmark hop count must be positive")
        return self.rtt_intercept_aci_cycles + self.rtt_hop_slope_aci_cycles * hops


@dataclass(frozen=True, slots=True)
class BatchedThroughputReference:
    """Measured rates for one fixed-size, back-to-back command schedule."""

    kernels: tuple[str, ...]
    message_bytes: int
    messages_per_stream: int
    simplex_tx_bytes_per_aci_cycle: float
    simplex_rx_bytes_per_aci_cycle: float
    dual_same_direction_bytes_per_aci_cycle: float
    dual_full_duplex_bytes_per_aci_cycle: float

    @property
    def payload_bytes_per_stream(self) -> int:
        return self.message_bytes * self.messages_per_stream

    @property
    def dual_full_duplex_efficiency(self) -> float:
        """Return aggregate rate relative to four simplex TX streams."""
        return self.dual_full_duplex_bytes_per_aci_cycle / (
            4 * self.simplex_tx_bytes_per_aci_cycle
        )


RB54_LATENCY_REFERENCE: Final[RTTBenchmarkReference] = RTTBenchmarkReference(
    kernel="noc_rb54.cpp",
    flit_bytes=512,
    default_burst_flits=8,
    hidden_serialization_through_bytes=4 * 1024,
    rtt_intercept_aci_cycles=204.0,
    rtt_hop_slope_aci_cycles=17.0,
    payload_samples=(
        (64, 221.0, 1),
        (128, 208.0, 1),
        (192, 221.0, 1),
        (256, 221.0, 1),
        (384, 221.0, 1),
        (512, 221.0, 1),
        (1024, 221.0, 2),
        (2048, 221.0, 4),
        (4096, 221.0, 8),
        (8192, 255.0, 16),
        (16384, 323.0, 32),
        (32768, 476.0, 64),
    ),
    hop_samples=(
        (1, 221.0),
        (2, 238.0),
        (3, 255.0),
        (4, 272.0),
        (5, 289.0),
        (6, 306.0),
        (7, 323.0),
        (8, 340.0),
        (9, 357.0),
        (10, 374.0),
    ),
)


NMC_32K_BATCH_REFERENCE: Final[BatchedThroughputReference] = BatchedThroughputReference(
    kernels=("noc_rb56.cpp", "noc_rb58.cpp"),
    message_bytes=32 * 1024,
    messages_per_stream=32,
    simplex_tx_bytes_per_aci_cycle=87.4,
    simplex_rx_bytes_per_aci_cycle=86.1,
    dual_same_direction_bytes_per_aci_cycle=171.0,
    dual_full_duplex_bytes_per_aci_cycle=339.7,
)
