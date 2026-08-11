from typing import List
from .definitions import Event, Trace, TimeSlice, TraceItem


def calc_intersection(L, R, l, r):
    nl, nr = max(L, l), min(R, r)
    return nr - nl + 1


def interval_merge(intervals):
    if not intervals:
        return []
    begin = 0
    merged = []
    for i in range(1, len(intervals)):
        if intervals[i - 1].end_time + 1 < intervals[i].start_time:
            merged.append((intervals[begin].start_time, intervals[i - 1].end_time))
            begin = i
    merged.append((intervals[begin].start_time, intervals[-1].end_time))
    return merged


def event2trace(id, start_time, end_time, events):
    merged = interval_merge(events)
    occupy = 0
    for interval in merged:
        occupy += calc_intersection(start_time, end_time - 1, interval[0], interval[1])
    util = 1.0 * occupy / (end_time - start_time) if end_time > start_time else 0.0
    return TraceItem(id=id, ultilization=util, op_num=len(events))


def get_slice_events(core_events, start, end):
    result = []
    for ev in core_events:
        if calc_intersection(start, end - 1, ev.start_time, ev.end_time) > 0:
            result.append(ev)
    return result


def process_events(simulation_time, time_slice_num, cores_events, links_events,
                   dma_events=None, mem_events=None):
    slice_len = simulation_time // time_slice_num + 1
    dma_events = dma_events or []
    mem_events = mem_events or []
    traces = Trace()
    for sid in range(time_slice_num):
        st = TimeSlice()
        s_start = sid * slice_len
        s_end = s_start + slice_len
        for cid, evs in enumerate(cores_events):
            st.cores.append(event2trace(cid, s_start, s_end,
                                        get_slice_events(evs, s_start, s_end)))
        for lid, evs in enumerate(links_events):
            st.links.append(event2trace(lid, s_start, s_end,
                                        get_slice_events(evs, s_start, s_end)))
        for did, evs in enumerate(dma_events):
            st.dma_links.append(event2trace(
                did, s_start, s_end, get_slice_events(evs, s_start, s_end)))
        for mid, evs in enumerate(mem_events):
            st.mem_bw.append(event2trace(
                mid, s_start, s_end, get_slice_events(evs, s_start, s_end)))
        traces.time_slices.append(st)
    return traces
