from collections.abc import Mapping, Sequence

from .noc import NoC, NoCLinkIdentity
from .utils.definitions import Event, NoCChannel, TimeSlice, Trace, TraceItem

Interval = tuple[float, float]


def calc_intersection(L: float, R: float, l: float, r: float) -> float:
    nl = max(L, l)
    nr = min(R, r)
    return nr - nl + 1


def interval_merge(intervals: Sequence[Event]) -> list[Interval]:
    if len(intervals) == 0:
        return []
    
    interval_begin = 0
    merged_intervals: list[Interval] = []

    for id in range(1, len(intervals)):
        if intervals[id-1].end_time+1 < intervals[id].start_time:
            merged_intervals.append((intervals[interval_begin].start_time, intervals[id-1].end_time))
            interval_begin = id

    # print(f"{len(intervals)} vs {interval_begin}")
    merged_intervals.append((intervals[interval_begin].start_time, intervals[-1].end_time))
    return merged_intervals


def event2trace(
    id: int,
    start_time: float,
    end_time: float,
    events: list[Event],
    fabric_id: NoCChannel | None = None,
) -> TraceItem:
    merged_intervals = interval_merge(events)
    occupy = 0.0

    for interval in merged_intervals:
        intersection = calc_intersection(start_time, end_time - 1, 
                                         interval[0], interval[1])
        occupy += intersection

    # print(f"{occupy} vs {end_time-start_time}")
    ultil = 1.0 * occupy / (end_time - start_time)
    
    item = TraceItem(
        id=id,
        slow=0,
        ultilization=ultil,
        op_num=len(events),
        fabric_id=fabric_id,
    )
    return item


def get_slice_events(
    core_events: list[Event],
    start_time: float,
    end_time: float,
) -> list[Event]:
    slice_events: list[Event] = []

    for event in core_events:
        intersection = calc_intersection(start_time, end_time - 1, 
                                         event.start_time, event.end_time)
        if intersection > 0:
            slice_events.append(event)

    return slice_events


def collect_noc_link_events(
    nocs: Mapping[NoCChannel, NoC],
) -> tuple[list[list[Event]], list[NoCLinkIdentity]]:
    """Collect directional link events in stable fabric/link order."""
    expected_fabrics = set(NoCChannel)
    if set(nocs) != expected_fabrics:
        missing = expected_fabrics - set(nocs)
        unexpected = set(nocs) - expected_fabrics
        raise ValueError(
            "NoC mapping must contain exactly all data fabrics; "
            f"missing={missing}, unexpected={unexpected}"
        )

    links_events: list[list[Event]] = []
    link_identities: list[NoCLinkIdentity] = []
    for fabric_id in NoCChannel:
        noc = nocs[fabric_id]
        if noc.fabric_id is not fabric_id:
            raise ValueError(
                f"{fabric_id.name} mapping contains {noc.fabric_id.name} NoC"
            )
        for local_link_id, link in enumerate(noc.r2r_links):
            identity = link.identity
            if identity.link_id != local_link_id:
                raise ValueError(
                    f"{fabric_id.name} link index {local_link_id} has identity "
                    f"{identity.link_id}"
                )
            links_events.append(link.utilization_events())
            link_identities.append(identity)

    return links_events, link_identities


def process_events(
    simulation_time: float,
    time_slice_num: int,
    cores_events: list[list[Event]],
    links_events: list[list[Event]],
    link_identities: Sequence[NoCLinkIdentity] | None = None,
) -> Trace:
    if link_identities is not None and len(link_identities) != len(links_events):
        raise ValueError(
            "link identity count must match the number of link event streams"
        )
    time_slice_len = simulation_time // time_slice_num + 1

    traces = Trace()

    for slice_id in range(time_slice_num):
        slice_trace = TimeSlice()
        slice_start = slice_id * time_slice_len
        slice_end = slice_start + time_slice_len

        # processing cores' traces in this time slice 
        core_num = len(cores_events)
        for core_id in range(core_num):
            slice_events = get_slice_events(cores_events[core_id], slice_start, slice_end)

            slice_core_trace = event2trace(core_id, slice_start, slice_end, slice_events)
            slice_trace.cores.append(slice_core_trace)

        # processing links' traces in this time slice 
        link_num = len(links_events)
        for trace_link_id in range(link_num):
            slice_events = get_slice_events(
                links_events[trace_link_id], slice_start, slice_end
            )
            identity = (
                None
                if link_identities is None
                else link_identities[trace_link_id]
            )

            slice_link_trace = event2trace(
                trace_link_id if identity is None else identity.link_id,
                slice_start,
                slice_end,
                slice_events,
                fabric_id=None if identity is None else identity.fabric_id,
            )
            slice_trace.links.append(slice_link_trace)

        traces.time_slices.append(slice_trace)

    return traces
