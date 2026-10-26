"""Source configuration and ordered-selection lowering for gathered down."""
from .expert_matrix import ExpertMatrixWork, SOURCE_COMMIT
from .workload import FrozenMoeWorkload, PROFILES


def derive_expert_work(profile, rank, order, *, locality=None):
    if profile != PROFILES['transformer-fused'] or type(rank) is not int or rank not in range(profile.ep_size):
        raise ValueError('expert Matrix source configuration/rank')
    order = tuple(order)
    count = len(order)
    if locality is not None:
        if locality not in ('repeated', 'spread'):
            raise ValueError('expert Matrix locality')
    elif order == (0,) * count:
        locality = 'repeated'
    elif order == tuple(index * 17 % profile.experts_per_rank for index in range(count)):
        locality = 'spread'
    else:
        raise ValueError('expert Matrix uncalibrated gather order')
    rows, columns, reduction = profile.down_per_pe
    tile_columns = 32
    tile_reduction = 128 * 8 // 4
    tiles_per_selection = rows * (columns // tile_columns) * (reduction // tile_reduction)
    scale_entries = (tiles_per_selection + 27) // 28
    entry_bytes = tile_columns * tile_reduction * 4 // 8
    descriptor_count = scale_entries + (tiles_per_selection - 1 + 15) // 16 + 1
    activation_stride = reduction + tile_columns * 4
    return ExpertMatrixWork(rank=rank, locality=locality, V=count, expert_order=order,
        shape=profile.down_per_pe, tile_shape=(1, tile_columns, tile_reduction),
        tiles=count * tiles_per_selection, activation_bytes=count * activation_stride,
        weight_bytes=count * (tiles_per_selection + scale_entries) * entry_bytes,
        table_bytes=count * descriptor_count * 8, output_bytes=count * rows * columns * 4)


def work_from_workload(workload, rank):
    workload = FrozenMoeWorkload.model_validate_json(workload.model_dump_json())
    if workload.identity.source_commit != SOURCE_COMMIT or rank not in workload.observed_ep_ranks:
        raise ValueError('expert Matrix workload source/rank')
    return derive_expert_work(workload.profile, rank,
        tuple(row.local_expert_id for row in workload.records if row.ep_rank == rank and row.status == 'valid'))


def reference_down(work, features, weights, feature_scales, weight_scales):
    import math
    work = ExpertMatrixWork.model_validate_json(work.model_dump_json())
    if (len(features), len(weights), len(feature_scales), len(weight_scales)) != (work.V, 96, work.V, 96):
        raise ValueError('expert Matrix reference shape')
    if any(len(row) != 256 for row in features) or any(len(expert) != 256 or any(len(row) != 256 for row in expert) for expert in weights) or any(len(row) != 256 for row in weight_scales):
        raise ValueError('expert Matrix reference shape')
    if any(type(value) is not int or not -128 <= value <= 127 for row in features for value in row) or any(type(value) is not int or not 0 <= value <= 15 for expert in weights for row in expert for value in row):
        raise ValueError('expert Matrix reference precision')
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (*feature_scales, *(value for row in weight_scales for value in row))):
        raise ValueError('expert Matrix reference scale')
    return tuple(tuple(sum(feature * weight for feature, weight in zip(features[index], row))
        * feature_scales[index] * weight_scales[expert][column]
        for column, row in enumerate(weights[expert])) for index, expert in enumerate(work.expert_order))
