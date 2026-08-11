import time

from .predictor import FailurePredictor
from ..configs.schemas.arch_config import ArchConfig
from .data_loader import ManycoreDatasetBuilder, TimeWindowConfig
from ..utils.timing_logger import log_timing


model_path = "models/best_model.pth"
overlap_size = 0
lookback = 2
device = "cuda"
threshold = 0.5
_PREDICTOR_CACHE = {}


def _get_predictor(mesh_x: int, mesh_y: int):
    cache_key = (mesh_x, mesh_y, device, model_path, threshold, lookback, overlap_size)
    predictor = _PREDICTOR_CACHE.get(cache_key)
    cache_hit = predictor is not None
    if predictor is None:
        predictor = FailurePredictor(
            model_path=model_path,
            mesh_x=mesh_x,
            mesh_y=mesh_y,
            window_size=1,
            overlap=overlap_size,
            lookback=lookback,
            device=device,
            threshold=threshold,
        )
        _PREDICTOR_CACHE[cache_key] = predictor
    return predictor, cache_hit


def detect(env_time: int, slice_num: int, arch_config: ArchConfig, core_events_json, link_events_json) -> tuple:
    detect_started = time.perf_counter()
    log_timing(
        "detect.start",
        env_time=int(round(env_time)),
        slice_num=slice_num,
        mesh_x=arch_config.noc.x,
        mesh_y=arch_config.noc.y,
        noc_type=arch_config.noc.type,
        core_event_count=len(core_events_json),
        link_event_count=len(link_events_json),
    )
    window_cfg = TimeWindowConfig()
    window_cfg.window_size = int(round(env_time)) // slice_num + 1
    window_cfg.overlap_size = 0
    window_cfg.min_events_per_window = 0

    mesh_x = arch_config.noc.x
    mesh_y = arch_config.noc.y
    type = arch_config.noc.type
    builder = ManycoreDatasetBuilder(mesh_x, mesh_y, type, window_cfg)

    build_started = time.perf_counter()
    graphs, meta = builder.build_from_trace(comp_events = core_events_json, 
                                            comm_events = link_events_json,
                                            failure_path = None)
    build_duration_ms = round((time.perf_counter() - build_started) * 1000, 3)

    predictor, cache_hit = _get_predictor(mesh_x, mesh_y)
    predict_started = time.perf_counter()
    core_probs, link_probs, results = predictor.predict(graphs=graphs, verbose=False)
    predict_duration_ms = round((time.perf_counter() - predict_started) * 1000, 3)
    total_duration_ms = round((time.perf_counter() - detect_started) * 1000, 3)

    log_timing(
        "detect",
        env_time=int(round(env_time)),
        slice_num=slice_num,
        mesh_x=mesh_x,
        mesh_y=mesh_y,
        noc_type=type,
        graph_count=len(graphs),
        core_event_count=len(core_events_json),
        link_event_count=len(link_events_json),
        cache_hit=cache_hit,
        build_duration_ms=build_duration_ms,
        predict_duration_ms=predict_duration_ms,
        total_duration_ms=total_duration_ms,
    )
    return core_probs, link_probs
