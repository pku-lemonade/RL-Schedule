"""Immutable hardware measurements used to validate the detailed simulator."""

from dataclasses import dataclass
from typing import Final


def _piecewise_min_latency_aci_cycles(
    payload_bytes: int,
    samples: tuple[tuple[int, float], ...],
    large_threshold_bytes: int,
    large_intercept_aci_cycles: float,
    service_bytes_per_aci_cycle: float,
    *,
    direction_name: str,
) -> float:
    """Interpolate measured minima and join them to one large-size trend."""
    if payload_bytes <= 0:
        raise ValueError(f"{direction_name} payload size must be positive")
    first_size, first_latency = samples[0]
    if payload_bytes <= first_size:
        return first_latency
    if payload_bytes >= large_threshold_bytes:
        return large_intercept_aci_cycles + (
            payload_bytes / service_bytes_per_aci_cycle
        )
    large_boundary = (
        large_threshold_bytes,
        large_intercept_aci_cycles
        + large_threshold_bytes / service_bytes_per_aci_cycle,
    )
    points = (*samples, large_boundary)
    for point_index in range(1, len(points)):
        lower = points[point_index - 1]
        upper = points[point_index]
        if payload_bytes <= upper[0]:
            fraction = (payload_bytes - lower[0]) / (upper[0] - lower[0])
            return lower[1] + fraction * (upper[1] - lower[1])
    raise AssertionError(f"{direction_name} latency profile is incomplete")


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


@dataclass(frozen=True, slots=True)
class SharedLinkContentionSample:
    """Measured isolated and contending rates for one rb53 message size."""

    message_bytes: int
    first_isolated_bytes_per_aci_cycle: float
    second_isolated_bytes_per_aci_cycle: float
    first_contending_bytes_per_aci_cycle: float
    second_contending_bytes_per_aci_cycle: float
    aggregate_contending_bytes_per_aci_cycle: float

    @property
    def aggregate_isolated_bytes_per_aci_cycle(self) -> float:
        return (
            self.first_isolated_bytes_per_aci_cycle
            + self.second_isolated_bytes_per_aci_cycle
        )

    @property
    def aggregate_retention(self) -> float:
        return (
            self.aggregate_contending_bytes_per_aci_cycle
            / self.aggregate_isolated_bytes_per_aci_cycle
        )


@dataclass(frozen=True, slots=True)
class SharedLinkContentionReference:
    """Measured rb53 schedule and its offered-load transition."""

    kernel: str
    messages_per_stream: int
    samples: tuple[SharedLinkContentionSample, ...]


@dataclass(frozen=True, slots=True)
class GMWDMABenchmarkReference:
    """Measured GM_WDMA command and data-service boundaries."""

    kernels: tuple[str, ...]
    descriptor_issue_aci_cycles: float
    outstanding_descriptors_per_channel: int
    small_message_completion_interval_aci_cycles: float
    zero_hop_operation_latency_aci_cycles: float
    operation_hop_slope_aci_cycles: float
    bulk_bytes_per_aci_cycle: float
    incast_bytes_per_aci_cycle_range: tuple[float, float]

    def operation_latency_aci_cycles(self, hops: int) -> float:
        """Return the calibrated paired-upload floor for a hop count."""
        if hops < 0:
            raise ValueError("GM_WDMA hop count cannot be negative")
        return (
            self.zero_hop_operation_latency_aci_cycles
            + self.operation_hop_slope_aci_cycles * hops
        )


@dataclass(frozen=True, slots=True)
class GMRDMABenchmarkReference:
    """Measured GM_RDMA download latency and throughput boundaries."""

    kernels: tuple[str, ...]
    aci_clock_ghz: float
    zero_hop_operation_latency_aci_cycles: float
    operation_hop_slope_aci_cycles: float
    service_bytes_per_aci_cycle: float
    zero_hop_bulk_gbps: float
    seven_hop_bulk_gbps: float
    outcast_gbps_range: tuple[float, float]

    def operation_latency_aci_cycles(self, hops: int) -> float:
        """Return the calibrated GM-to-PE completion floor for a hop count."""
        if hops < 0:
            raise ValueError("GM_RDMA hop count cannot be negative")
        return (
            self.zero_hop_operation_latency_aci_cycles
            + self.operation_hop_slope_aci_cycles * hops
        )

    def bytes_per_aci_cycle_to_gbps(self, rate: float) -> float:
        """Convert an ACI-domain byte rate to decimal GB/s."""
        return rate * self.aci_clock_ghz


@dataclass(frozen=True, slots=True)
class DDRDMABenchmarkReference:
    """Measured DDR direction-specific service and latency evidence."""

    kernels: tuple[str, ...]
    wdma_service_bytes_per_aci_cycle: float
    rdma_service_bytes_per_aci_cycle: float
    wdma_min_latency_samples: tuple[tuple[int, float], ...]
    wdma_large_min_threshold_bytes: int
    wdma_large_min_intercept_aci_cycles: float
    rdma_min_latency_samples: tuple[tuple[int, float], ...]
    rdma_large_min_threshold_bytes: int
    rdma_large_min_intercept_aci_cycles: float
    rdma_hop_slope_aci_cycles: float

    def wdma_min_operation_latency_aci_cycles(self, payload_bytes: int) -> float:
        """Return the size-aware minimum paired-upload latency."""
        return _piecewise_min_latency_aci_cycles(
            payload_bytes,
            self.wdma_min_latency_samples,
            self.wdma_large_min_threshold_bytes,
            self.wdma_large_min_intercept_aci_cycles,
            self.wdma_service_bytes_per_aci_cycle,
            direction_name="DDR WDMA",
        )

    def rdma_min_operation_latency_aci_cycles(
        self,
        payload_bytes: int,
        hops: int,
    ) -> float:
        """Return the size- and hop-aware minimum paired-download latency."""
        if hops < 0:
            raise ValueError("DDR RDMA hop count cannot be negative")
        zero_hop_latency = _piecewise_min_latency_aci_cycles(
            payload_bytes,
            self.rdma_min_latency_samples,
            self.rdma_large_min_threshold_bytes,
            self.rdma_large_min_intercept_aci_cycles,
            self.rdma_service_bytes_per_aci_cycle,
            direction_name="DDR RDMA",
        )
        return zero_hop_latency + hops * self.rdma_hop_slope_aci_cycles


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


RB53_CONTENTION_REFERENCE: Final[SharedLinkContentionReference] = (
    SharedLinkContentionReference(
        kernel="noc_rb53.cpp",
        messages_per_stream=16,
        samples=(
            SharedLinkContentionSample(
                message_bytes=4 * 1024,
                first_isolated_bytes_per_aci_cycle=27.2,
                second_isolated_bytes_per_aci_cycle=27.2,
                first_contending_bytes_per_aci_cycle=27.2,
                second_contending_bytes_per_aci_cycle=27.2,
                aggregate_contending_bytes_per_aci_cycle=54.4,
            ),
            SharedLinkContentionSample(
                message_bytes=8 * 1024,
                first_isolated_bytes_per_aci_cycle=44.5,
                second_isolated_bytes_per_aci_cycle=44.4,
                first_contending_bytes_per_aci_cycle=44.7,
                second_contending_bytes_per_aci_cycle=44.5,
                aggregate_contending_bytes_per_aci_cycle=89.2,
            ),
            SharedLinkContentionSample(
                message_bytes=16 * 1024,
                first_isolated_bytes_per_aci_cycle=64.1,
                second_isolated_bytes_per_aci_cycle=64.2,
                first_contending_bytes_per_aci_cycle=55.5,
                second_contending_bytes_per_aci_cycle=55.0,
                aggregate_contending_bytes_per_aci_cycle=110.5,
            ),
        ),
    )
)


GM_WDMA_REFERENCE: Final[GMWDMABenchmarkReference] = GMWDMABenchmarkReference(
    kernels=(
        "noc_gm_ul_bench.cpp",
        "noc_gm_ul_allpe.cpp",
        "noc_rb55.cpp",
    ),
    descriptor_issue_aci_cycles=40.0,
    outstanding_descriptors_per_channel=4,
    small_message_completion_interval_aci_cycles=273.0,
    zero_hop_operation_latency_aci_cycles=138.0,
    operation_hop_slope_aci_cycles=17.0,
    bulk_bytes_per_aci_cycle=110.0,
    incast_bytes_per_aci_cycle_range=(100.0, 120.0),
)


GM_RDMA_REFERENCE: Final[GMRDMABenchmarkReference] = GMRDMABenchmarkReference(
    kernels=("noc_gm_download.cpp", "noc_gm_bench.cpp"),
    aci_clock_ghz=1.125,
    zero_hop_operation_latency_aci_cycles=246.0,
    operation_hop_slope_aci_cycles=17.0,
    service_bytes_per_aci_cycle=110.0,
    zero_hop_bulk_gbps=119.0,
    seven_hop_bulk_gbps=113.0,
    outcast_gbps_range=(120.0, 125.0),
)


DDR_DMA_REFERENCE: Final[DDRDMABenchmarkReference] = DDRDMABenchmarkReference(
    kernels=(
        "noc_ddr_ul_generic.cpp",
        "noc_ddr_dl_generic.cpp",
        "noc_ddr_ul_dualch.cpp",
    ),
    wdma_service_bytes_per_aci_cycle=117.0,
    rdma_service_bytes_per_aci_cycle=102.0,
    wdma_min_latency_samples=(
        (512, 193.0),
        (4 * 1024, 346.0),
        (16 * 1024, 465.0),
        (32 * 1024, 601.0),
        (64 * 1024, 890.0),
        (128 * 1024, 1213.0),
        (256 * 1024, 2335.0),
    ),
    wdma_large_min_threshold_bytes=512 * 1024,
    wdma_large_min_intercept_aci_cycles=90.0,
    rdma_min_latency_samples=(
        (512, 426.0),
        (4 * 1024, 574.0),
        (16 * 1024, 693.0),
        (32 * 1024, 880.0),
        (64 * 1024, 1169.0),
        (128 * 1024, 1747.0),
        (256 * 1024, 2988.0),
    ),
    rdma_large_min_threshold_bytes=512 * 1024,
    rdma_large_min_intercept_aci_cycles=337.0,
    rdma_hop_slope_aci_cycles=17.0,
)
