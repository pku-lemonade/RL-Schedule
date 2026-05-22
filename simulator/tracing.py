import json
from enum import Enum, auto
from enum import IntEnum
from typing import List
from pydantic import BaseModel
from pydantic import ValidationError


from utils.definitions import Trace, TimeSlice, TraceItem, Event


def calc_intersection(L: int, R: int, l: int, r: int):
    nl = max(L, l)
    nr = min(R, r)
    return nr - nl + 1


def interval_merge(intervals):
    if len(intervals) == 0:
        return []
    
    interval_begin = 0
    merged_intervals = []

    for id in range(1, len(intervals)):
        if intervals[id-1].end_time+1 < intervals[id].start_time:
            merged_intervals.append((intervals[interval_begin].start_time, intervals[id-1].end_time))
            interval_begin = id

    # print(f"{len(intervals)} vs {interval_begin}")
    merged_intervals.append((intervals[interval_begin].start_time, intervals[-1].end_time))
    return merged_intervals


def event2trace(id: int, start_time: int, end_time: int, events: List[Event]):
    merged_intervals = interval_merge(events)
    occupy = 0

    for interval in merged_intervals:
        intersection = calc_intersection(start_time, end_time - 1, 
                                         interval[0], interval[1])
        occupy += intersection

    # print(f"{occupy} vs {end_time-start_time}")
    ultil = 1.0 * occupy / (end_time - start_time)
    
    item = TraceItem(id=id, slow=0, ultilization=ultil, op_num=len(events))
    return item


def get_slice_events(core_events: List[Event], start_time: int, end_time: int):
    slice_events = []

    for event in core_events:
        intersection = calc_intersection(start_time, end_time - 1, 
                                         event.start_time, event.end_time)
        if intersection > 0:
            slice_events.append(event)

    return slice_events


def process_events(simulation_time: int, time_slice_num: int,
                   cores_events: List[List[Event]], links_events: List[List[Event]]) -> Trace:
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
        for link_id in range(link_num):
            slice_events = get_slice_events(links_events[link_id], slice_start, slice_end)

            slice_link_trace = event2trace(link_id, slice_start, slice_end, slice_events)
            slice_trace.links.append(slice_link_trace)

        traces.time_slices.append(slice_trace)

    return traces