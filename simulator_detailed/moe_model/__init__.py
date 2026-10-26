"""Phase-0 contracts for the T81 MoE decode architecture model.

This package intentionally contains no runtime lowering, packet execution, or
parameter sweep implementation.  Those belong to later gated phases.
"""

from .controllability import (
    ParameterControllabilityMatrix,
    ParameterControllabilityRow,
    REQUIRED_PARAMETER_GROUPS,
    SourceLocation,
    freeze_controllability_matrix,
)
from .costs import (
    CalibrationManifest,
    CalibrationScene,
    CostProvider,
    CostQuote,
    CostRequest,
    WorkItem,
    freeze_calibration_manifest,
    freeze_cost_request,
)
from .hardware import (
    ApplicabilityDomain,
    BindingArtifact,
    HardwareMutation,
    HardwareParameter,
    HardwareParameterSet,
    MutationScope,
    ParameterValue,
    REQUIRED_BINDING_ARTIFACTS,
    TopologyBindingInventory,
    apply_mutations,
    freeze_hardware_parameters,
    freeze_topology_binding_inventory,
)
from .mfu import (
    DecodeBaselineManifest,
    EngineUtilizationRequest,
    MetricResult,
    PeakConvention,
    TimeBoundary,
    compute_full_decode_mfu,
    compute_issued_engine_utilization,
    compute_moe_local_mfu,
    compute_useful_model_mfu,
    freeze_decode_baseline,
)
from .workload import (
    FrozenMoeWorkload,
    MoeFlopAccounting,
    MoeProfileSpec,
    PROFILES,
    RankFlopAccounting,
    RankValidCount,
    RouteRecord,
    RouteTraceIdentity,
    TokenHotspot,
    flop_accounting,
    freeze_workload,
    stable_sha256,
)
from .adalink import (
    AdalinkAction,
    AdalinkSchedule,
    CollectiveChunk,
    build_direct_schedule,
    build_ring_schedule,
    freeze_schedule,
)
from .adalink_binding import (
    AdalinkCostTable,
    AdalinkParameterBinding,
    AdalinkRankBinding,
    AdalinkRouteBinding,
    LocalReduceCost,
    RankCclConfig,
    SuppliedAdalinkBinding,
    freeze_adalink_binding,
    freeze_adalink_cost_table,
)
from .adalink_execute import (
    AdalinkExecution,
    CompiledAdalink,
    compare_link_mutation,
    compile_adalink,
    execute_adalink,
)
from .phase3_fixture import (
    Phase3FixtureBundle,
    StageActionMap,
    StageFlowMap,
    build_phase3_fixture,
)
from .phase3_fixture_execute import (
    CompiledPhase3Fixture,
    Phase3FixtureExecution,
    compare_fixture_mutation,
    compile_phase3_fixture,
    execute_phase3_fixture,
)
from .exploration_pipeline import (
    BaselineFreezeManifest,
    SweepCase,
    SweepPlan,
    analyze,
    cli as exploration_cli,
    freeze_baseline,
    freeze_sweep_plan,
    run_baseline,
    run_sweep,
    status as exploration_status,
    validate_inputs,
)
from .component_calibration import (
    ActionDomain,
    CalibrationResolution,
    CalibrationResolutionRequest,
    ComponentCalibrationBundle,
    ComponentCalibrationEntry,
    build_component_calibration_bundle,
    coverage_matrix,
    gap_contracts,
    resolution_issues,
    resolve_calibration,
)
from .matrix_v_domain import (
    MatrixVAnchor,
    MatrixVAnchorSlice,
    MatrixVDomainFormula,
    MatrixVExtrapolationSlice,
    freeze_matrix_v_formula,
)
from .vector_v_domain import (
    VectorVAnchor,
    VectorVAnchorSlice,
    VectorVDomainFormula,
    freeze_vector_v_formula,
)
from .chain_v_domain import (
    ChainBoundaryAffine,
    ChainPayloadFormula,
    ChainScenarioFormula,
    freeze_chain_payload_formula,
)
from .chain_execute import (
    ChainService, ChainParameterTarget, ChainRuntimeBinding,
    freeze_chain_runtime_binding, compile_chain, execute_chain,
)
from .gm_v_domain import GMBoundaryAffine,GMServiceDomainFormula,freeze_gm_service_formula
from .datanoc_v_domain import DataNocBoundaryAffine,DataNocDomainFormula,freeze_datanoc_formula
from .attn_output_norm import (
    AttnOutputNormWork, AttnOutputNormPeTiming, AttnOutputNormFormula,
    AttnOutputNormDomain, AttnOutputNormBinding,
    freeze_attn_output_norm_formula, freeze_attn_output_norm_domain,
    freeze_attn_output_norm_binding, resolve_attn_output_norm,
    attn_output_norm_actions, execute_attn_output_norm,
)
from .e4_e5_handoff import (
    E4E5HandoffWork, E4E5PeTiming, E4E5HandoffFormula,
    E4E5ObservedEvent, E4E5HandoffDomain, E4E5HandoffBinding,
    freeze_e4_e5_formula, freeze_e4_e5_domain, freeze_e4_e5_binding,
    resolve_e4_e5_handoff, e4_e5_handoff_actions, execute_e4_e5_handoff,
)
from .e0_e1_handoff import (
    E0E1HandoffWork, E0E1PeTiming, E0E1HandoffFormula,
    E0E1ObservedEvent, E0E1HandoffDomain, E0E1HandoffBinding,
    freeze_e0_e1_formula, freeze_e0_e1_domain, freeze_e0_e1_binding,
    resolve_e0_e1_handoff, e0_e1_handoff_actions, execute_e0_e1_handoff,
)
from .e2_e3_handoff import (
    E2E3HandoffWork, E2E3PeTiming, E2E3HandoffFormula,
    E2E3ObservedEvent, E2E3HandoffDomain, E2E3HandoffBinding,
    freeze_e2_e3_formula, freeze_e2_e3_domain, freeze_e2_e3_binding,
    resolve_e2_e3_handoff, e2_e3_handoff_actions, execute_e2_e3_handoff,
)

from .e1_e2_handoff import (
    E1E2HandoffWork, E1E2ObservedEvent, E1E2PeTiming, E1E2HandoffFormula,
    E1E2HandoffDomain, E1E2HandoffBinding, freeze_e1_e2_formula,
    freeze_e1_e2_domain, freeze_e1_e2_binding, resolve_e1_e2_handoff,
    e1_e2_handoff_actions, execute_e1_e2_handoff,
)

from .scatter_handoff_contract import (
    ScatterHandoffWork, ScatterAddressIdentity, ScatterHandoffEvent,
    extract_scatter_handoff_source, require_correlated_scatter_frontier,
    scatter_handoff_blockers, require_scatter_handoff_timing,
)

from .e3_e4_handoff import (
    E3E4HandoffWork, E3E4ObservedEvent, E3E4PeTiming, E3E4HandoffFormula,
    E3E4HandoffDomain, E3E4HandoffBinding, extract_e3_e4_source,
    freeze_e3_e4_formula, freeze_e3_e4_domain, freeze_e3_e4_binding,
    resolve_e3_e4_handoff, e3_e4_handoff_actions, execute_e3_e4_handoff,
)

__all__ = [name for name in globals() if not name.startswith("_")]

from .sync_resource import SyncResource, SyncResourceBinding, SyncLocalFormula, freeze_sync_resource_binding, freeze_sync_local_formula, execute_sync_resources, mutate_sync_resource
from .sync_cost import sync_formula_resource, require_sync_formula_domain

from .adalink_protocol import CommCounterAction, CommCounterProgram, freeze_comm_counter_program, execute_consuming_adalink, mutate_local_reduce
from .adalink_plan import adalink_operation_inventory

from .r7_r8_handoff import (
    R7R8HandoffWork, R7R8ObservedEvent, R7R8PeTiming, R7R8HandoffFormula,
    R7R8HandoffDomain, R7R8HandoffBinding, extract_r7_r8_source,
    freeze_r7_r8_formula, freeze_r7_r8_domain, freeze_r7_r8_binding,
    resolve_r7_r8_handoff, r7_r8_handoff_actions, execute_r7_r8_handoff,
)

from .component_calibration import execute_resolved_r7_r8_handoff

from .r9_count_handoff import (R9CountHandoffWork,R9CountObservedEvent,R9CountPeTiming,R9CountHandoffFormula,R9CountHandoffDomain,R9CountHandoffBinding,extract_r9_count_source,freeze_r9_count_formula,freeze_r9_count_domain,freeze_r9_count_binding,resolve_r9_count_handoff,r9_count_handoff_actions,execute_r9_count_handoff)
from .component_calibration import execute_resolved_r9_count_handoff

from .count_token_handoff import (CountTokenHandoffWork,CountTokenObservedEvent,CountTokenPeTiming,CountTokenHandoffFormula,CountTokenHandoffDomain,CountTokenHandoffBinding,extract_count_token_source,freeze_count_token_formula,freeze_count_token_domain,freeze_count_token_binding,resolve_count_token_handoff,count_token_handoff_actions,execute_count_token_handoff)
from .component_calibration import execute_resolved_count_token_handoff

from .token_id_fill import (TokenIdFillWork,TokenIdFillEvent,TokenIdPeFormula,TokenIdFillFormula,TokenIdFillDomain,TokenIdFillBinding,extract_token_id_fill_source,freeze_token_id_fill_formula,freeze_token_id_fill_domain,freeze_token_id_fill_binding,resolve_token_id_fill,token_id_fill_actions,execute_token_id_fill)
from .component_calibration import execute_resolved_token_id_fill

from .router_rank_domains import (RouterRankWork,RouterRankDomain,RouterRankBinding,RankPeParameters,RankSegmentFormula,rank_work,freeze_rank_formula,freeze_rank_domain,freeze_rank_binding,resolve_rank_domain,rank_actions,execute_rank_domain,execute_rank_composition)
from .component_calibration import execute_resolved_router_rank

from .count_fill import (CountFillWork,CountFillFormula,CountFillDomain,CountFillBinding,count_work,extract_count_fill,freeze_count_formula,freeze_count_domain,freeze_count_binding,resolve_count_fill,count_actions,execute_count_fill)
from .component_calibration import execute_resolved_count_fill

from .scalar_tail import (ScalarTailWork,ScalarTailFormula,ScalarTailDomain,ScalarTailBinding,tail_work,extract_tail_source,freeze_tail_formula,freeze_tail_domain,freeze_tail_binding,resolve_scalar_tail,tail_actions,execute_scalar_tail)
from .component_calibration import execute_resolved_scalar_tail

from .count_scan import (CountScanWork,CountScanFormula,CountScanDomain,CountScanBinding,scan_work,extract_scan_source,freeze_scan_formula,freeze_scan_domain,freeze_scan_binding,resolve_count_scan,scan_actions,execute_count_scan)
from .component_calibration import execute_resolved_count_scan
