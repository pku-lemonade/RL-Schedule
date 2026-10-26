"""Source-bound comm-id17 synchronization; no collective, Scalar or data cost."""
from dataclasses import dataclass
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from .workload import FrozenRecord, stable_sha256
from .timebase import TimebaseDomain
from ..configs.schemas.generic_graph import GenericSystemGraph
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system
from ..runtime_context import RuntimeContext


SOURCE_COMMIT = '094bcd25e985a4c836fd5521cccd61b14d911aac'
CALLSITE = 'transformer_layer.cpp:435-439->308-311'
OWNER = 'M07.transformer-fused.bridge.commid17'
PRODUCERS = tuple(range(4, 18))
WAITERS = tuple(range(4))
EXCLUDED = ('Matrix', 'Vector', 'GM', 'DataNoC', 'multicast', 'Scalar',
            'host', 'attention_allreduce', 'E7', 'receiver_outer_wait', 'successor')


def checked(record):
    return type(record).model_validate_json(record.model_dump_json())


def frozen(record_type, **values):
    raw = json.loads(json.dumps(values, default=lambda value: value.model_dump(mode='json')))
    if record_type is BridgeFormula:
        raw.setdefault('fanout_ns', 0.)
    raw['identity_sha256'] = stable_sha256(raw)
    return record_type.model_validate_json(json.dumps(raw))


class BridgeFormula(FrozenRecord):
    family: Literal['release-ready-acquire-overlap-max-v2']
    release_ns: float = Field(ge=0)
    fanout_ns: float = Field(ge=0)
    count_ns: tuple[float, float, float, float]
    acquire_ns: tuple[float, float, float, float]
    calibration_ids: tuple[str, ...]
    identity_sha256: str

    @model_validator(mode='after')
    def valid(self):
        values = (self.release_ns, self.fanout_ns) + self.count_ns + self.acquire_ns
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError('nonfinite/negative synchronization service')
        if not self.calibration_ids or len(set(self.calibration_ids)) != len(self.calibration_ids):
            raise ValueError('calibration identities missing/duplicated')
        if stable_sha256(self.model_dump(mode='json', exclude={'identity_sha256'})) != self.identity_sha256:
            raise ValueError('formula hash drift')
        return self


class BridgeWork(FrozenRecord):
    profile: Literal['transformer-fused'] = 'transformer-fused'
    stage: Literal['ATTN_TO_MOE.commid17'] = 'ATTN_TO_MOE.commid17'
    producer_stage: Literal['attention.allreduce.release_issue'] = 'attention.allreduce.release_issue'
    consumer_stage: Literal['bridge.first_receiver_outer_acquire'] = 'bridge.first_receiver_outer_acquire'
    source_commit: Literal[SOURCE_COMMIT] = SOURCE_COMMIT
    source_callsite: Literal[CALLSITE] = CALLSITE
    producers: tuple[int, ...] = PRODUCERS
    waiters: tuple[int, ...] = WAITERS
    expected_count: Literal[14] = 14
    increment: Literal[1] = 1
    comm_id: Literal[17] = 17
    counter_kind: Literal['COMM_PRODUCE_CNT'] = 'COMM_PRODUCE_CNT'
    source_entry: Literal['ADL_FUNC'] = 'ADL_FUNC'
    waiter_entry: Literal['DMA_CHANNEL_1'] = 'DMA_CHANNEL_1'
    rank: Literal[0] = 0
    pe: Literal['none'] = 'none'
    ordering: Literal['release-visibility-count-acquire-fifo-successor'] = 'release-visibility-count-acquire-fifo-successor'
    unit: Literal['reported_ns'] = 'reported_ns'
    boundary: Literal['native-release-issue->native-counted-acquire-end'] = 'native-release-issue->native-counted-acquire-end'

    @model_validator(mode='after')
    def valid(self):
        if self.producers != PRODUCERS or self.waiters != WAITERS:
            raise ValueError('source participant set mismatch')
        return self


class BridgeDomain(FrozenRecord):
    work: BridgeWork
    formula: BridgeFormula
    timebase: TimebaseDomain
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    compiled_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    observer_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    validation_ids: tuple[str, ...]
    status: Literal['validated_simulation']
    identity_sha256: str

    @model_validator(mode='after')
    def valid(self):
        if not self.validation_ids or set(self.validation_ids) & set(self.formula.calibration_ids):
            raise ValueError('validation evidence missing/overlapping')
        if (self.timebase.source_unit, self.timebase.boundary_id, self.timebase.accepted_work_values,
            self.timebase.status, self.timebase.seconds_per_source_unit) != (
                self.work.unit, self.work.boundary, (14,), 'validated_simulation', 1e-9):
            raise ValueError('exact simulation timebase required')
        if stable_sha256(self.model_dump(mode='json', exclude={'identity_sha256'})) != self.identity_sha256:
            raise ValueError('domain hash drift')
        return self


class BridgeRequest(FrozenRecord):
    work: BridgeWork
    source_sha256: str
    compiled_sha256: str
    observer_sha256: str
    model_library_sha256: str
    systemc_library_sha256: str
    physical: Literal[False] = False


def resolve_bridge(domains, request):
    request = checked(request)
    matches = []
    for candidate in domains:
        domain = checked(candidate)
        if (domain.work == request.work and all(getattr(domain, name) == getattr(request, name)
                for name in ('source_sha256', 'compiled_sha256', 'observer_sha256')) and
                domain.timebase.model_library_sha256 == request.model_library_sha256 and
                domain.timebase.systemc_library_sha256 == request.systemc_library_sha256):
            matches.append(domain)
    if len(matches) != 1:
        raise ValueError(f'one exact bridge domain required; found {len(matches)}')
    return matches[0]


class SyncGroup(FrozenRecord):
    group_id: str = Field(min_length=1)
    rank: int = Field(ge=0, le=3)
    comm_id: int = Field(ge=0, le=31)
    generation: int = Field(ge=0)
    producers: tuple[int, ...]
    waiters: tuple[int, ...]
    expected_count: int = Field(gt=0, le=14)
    producer_dependencies: dict[int, str]
    acquire_dependencies: dict[int, str]

    @model_validator(mode='after')
    def valid(self):
        if (not self.producers or tuple(sorted(set(self.producers))) != self.producers or
                not set(self.producers) <= set(PRODUCERS) or self.expected_count != len(self.producers) or
                not self.waiters or tuple(sorted(set(self.waiters))) != self.waiters or
                not set(self.waiters) <= set(WAITERS) or
                set(self.producer_dependencies) != set(self.producers) or
                set(self.acquire_dependencies) != set(self.waiters) or
                not all(self.producer_dependencies.values()) or not all(self.acquire_dependencies.values())):
            raise ValueError('participant/count/dependency mismatch')
        if len(set(self.producer_dependencies.values())) != len(self.producers):
            raise ValueError('distinct producer issue boundaries required')
        return self


class BridgeBoundary(FrozenRecord):
    transaction_id: str
    role: Literal['producer', 'waiter']
    node: int
    rank: Literal[0] = 0
    pe: Literal['none'] = 'none'
    entry: str
    stage: str
    comm_id: Literal[17] = 17
    unit: Literal['reported_ns'] = 'reported_ns'
    source_sha256: str
    compiled_sha256: str

    @model_validator(mode='after')
    def valid(self):
        expected = ('ADL_FUNC', 'attention.allreduce.release_issue') if self.role == 'producer' else (
            'DMA_CHANNEL_1', 'bridge.counted_acquire.issue')
        if (self.entry, self.stage) != expected or self.node not in (PRODUCERS if self.role == 'producer' else WAITERS):
            raise ValueError('boundary stage/node/entry mismatch')
        return self


def release_resource(rank, producer):
    return f'sync.bridge.rank{rank}.ADALINK{producer}.ADL_FUNC'


def waiter_resource(rank, waiter):
    return f'sync.bridge.rank{rank}.GM_RDMA{waiter}.DMA_CHANNEL_1'


class BridgeBinding(FrozenRecord):
    graph: dict
    timing: tuple[dict, ...] = ()
    resources: dict[str, str]
    domain_sha256: str
    source_ref: str = Field(min_length=1)
    identity_sha256: str

    @model_validator(mode='after')
    def valid(self):
        graph = GenericSystemGraph.model_validate_json(json.dumps(self.graph))
        units = {unit.unit_id: unit.node_id for unit in graph.execution_units}
        if len(set(self.resources.values())) != len(self.resources):
            raise ValueError('aliased synchronization resources')
        for resource, unit in self.resources.items():
            parts = resource.split('.')
            if (len(parts) != 5 or parts[:2] != ['sync', 'bridge'] or
                    unit not in units or units[unit] != '.'.join(parts[2:4])):
                raise ValueError('resource/node/rank/entry binding mismatch')
            rank = int(parts[2].removeprefix('rank'))
            valid = {release_resource(rank, producer) for producer in PRODUCERS}
            valid |= {waiter_resource(rank, waiter) for waiter in WAITERS}
            if resource not in valid or rank not in range(4):
                raise ValueError('unknown source resource')
        if stable_sha256(self.model_dump(mode='json', exclude={'identity_sha256'})) != self.identity_sha256:
            raise ValueError('binding hash drift')
        return self


def parameters(formula, groups):
    output = {}
    for group in groups:
        for producer in group.producers:
            output[release_resource(group.rank, producer) + '.release_ns'] = formula.release_ns
            output[release_resource(group.rank, producer) + '.fanout_ns'] = formula.fanout_ns
        for waiter in group.waiters:
            resource = waiter_resource(group.rank, waiter)
            output[resource + '.count_ns'] = formula.count_ns[waiter]
            output[resource + '.acquire_ns'] = formula.acquire_ns[waiter]
    return output


def predict(formula, releases, acquires):
    formula = checked(formula)
    terminals = {}
    for waiter, acquire in acquires.items():
        ready = 0.0
        for producer, start in sorted(releases.items(), key=lambda item: (item[1], item[0])):
            ready = max(ready, start + formula.release_ns + int(waiter) * formula.fanout_ns) + formula.count_ns[int(waiter)]
        terminals[int(waiter)] = max(ready, acquire + formula.acquire_ns[int(waiter)])
    return terminals


class BridgeRuntime(RuntimeContext):
    def __init__(self, plan, consumptions):
        super().__init__(plan)
        self.consumptions = consumptions
        self.consumed = {}

    def _finish(self, transaction_id, kind, start, wait_cycles, hops=(), service=None):
        if transaction_id in self.consumptions:
            counter_id, count = self.consumptions[transaction_id]
            counter = self.bus.counter(counter_id)
            if counter.value != count or counter_id in self.consumed:
                raise ValueError('count conservation violation')
            self.consumed[counter_id] = count
            counter.add(-count)
        super()._finish(transaction_id, kind, start, wait_cycles, hops, service)


@dataclass(frozen=True)
class BridgeExecution:
    result: object
    runtime: object
    receipt: dict


def execute_bridge(formula, binding, groups, external_transactions, *, domain=None,
                   production=True, changes=None, cancel=None, max_cycles=100000, boundaries=()):
    formula, binding = checked(formula), checked(binding)
    groups = tuple(checked(group) for group in groups)
    if not groups or len({group.group_id for group in groups}) != len(groups):
        raise ValueError('unique synchronization groups required')
    namespaces = {(group.rank, group.comm_id, group.generation) for group in groups}
    if len(namespaces) != len(groups):
        raise ValueError('duplicate comm-id generation')
    if production:
        if domain is None:
            raise ValueError('accepted domain required')
        domain = checked(domain)
        if binding.domain_sha256 != domain.identity_sha256 or formula != domain.formula:
            raise ValueError('domain/binding/formula mismatch')
        for group in groups:
            if (group.rank, group.comm_id, group.producers, group.waiters, group.expected_count) != (
                    domain.work.rank, 17, PRODUCERS, WAITERS, 14):
                raise ValueError('production group differs from source')
        boundaries = tuple(checked(boundary) for boundary in boundaries)
        identities = {boundary.transaction_id: boundary for boundary in boundaries}
        expected_ids = {identity for group in groups for mapping in (
            group.producer_dependencies, group.acquire_dependencies) for identity in mapping.values()}
        if set(identities) != expected_ids or len(identities) != len(boundaries):
            raise ValueError('exact source issue boundary inventory required')
        for group in groups:
            for role, mapping in (('producer', group.producer_dependencies), ('waiter', group.acquire_dependencies)):
                for node, identity in mapping.items():
                    boundary = identities[identity]
                    if (boundary.role, boundary.node, boundary.source_sha256, boundary.compiled_sha256) != (
                            role, node, domain.source_sha256, domain.compiled_sha256):
                        raise ValueError('source/compiled/participant boundary mismatch')
            for waiter in group.waiters:
                terminal = f'bridge.{group.group_id}.acquire.{waiter}'
                successors = [transaction for transaction in external_transactions if terminal in transaction.get('depends_on', ())]
                expected = f'rank{group.rank}.bridge.gm{waiter}.first_receiver_outer_acquire.{group.group_id}'
                if len(successors) != 1 or successors[0]['transaction_id'] != expected or successors[0]['depends_on'] != [terminal]:
                    raise ValueError('unique real source successor required')
    values = parameters(formula, groups)
    for path, value in (changes or {}).items():
        if path not in values or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError('unknown/nonfinite/local parameter intervention')
        values[path] = value
    transactions = [dict(transaction) for transaction in external_transactions]
    external_ids = {transaction['transaction_id'] for transaction in transactions}
    owned_units = set(binding.resources.values())
    if len(external_ids) != len(transactions) or any(
            transaction['transaction_id'].startswith('bridge.') or transaction.get('unit_id') in owned_units
            for transaction in transactions):
        raise ValueError('duplicate timing owner')
    counters, consumptions, terminals = [], {}, []

    def compute(identity, resource, duration, dependencies):
        if resource not in binding.resources:
            raise ValueError('missing synchronization resource')
        if duration == 0:
            counter = identity + '.zero'
            counters.append(dict(counter_id=counter, initial_value=0))
            transactions.append(dict(kind='signal', transaction_id=identity, counter_id=counter,
                                     delta=1, depends_on=list(dependencies)))
            return
        transactions.append(dict(kind='compute', transaction_id=identity, unit_id=binding.resources[resource],
                                 duration_cycles=duration, depends_on=list(dependencies)))

    for group in groups:
        prefix = f'bridge.{group.group_id}'
        dependencies = set(group.producer_dependencies.values()) | set(group.acquire_dependencies.values())
        if not dependencies <= external_ids:
            raise ValueError('missing predecessor/native issue boundary')
        releases = {}
        for producer in group.producers:
            resource = release_resource(group.rank, producer)
            identity = f'{prefix}.release.{producer}'
            compute(identity, resource, values[resource + '.release_ns'], (group.producer_dependencies[producer],))
            for ordinal, waiter in enumerate(group.waiters):
                if ordinal:
                    previous = identity
                    identity = f'{prefix}.fanout.{producer}.{waiter}'
                    compute(identity, resource, values[resource + '.fanout_ns'], (previous,))
                releases[producer, waiter] = identity
        group_ends = []
        for waiter in group.waiters:
            resource = waiter_resource(group.rank, waiter)
            prepared = f'{prefix}.acquire_service.{waiter}'
            compute(prepared, resource, values[resource + '.acquire_ns'], (group.acquire_dependencies[waiter],))
            counter = f'{prefix}.rank{group.rank}.comm{group.comm_id}.generation{group.generation}.gm{waiter}'
            counters.append(dict(counter_id=counter, initial_value=0))
            for producer in group.producers:
                identity = f'{prefix}.count.{waiter}.{producer}'
                compute(identity, resource, values[resource + '.count_ns'], (releases[producer, waiter],))
                transactions.append(dict(kind='signal', transaction_id=identity + '.publish',
                                         counter_id=counter, delta=1, depends_on=[identity]))
            wait = f'{prefix}.wait.{waiter}'
            transactions.append(dict(kind='wait', transaction_id=wait, counter_id=counter,
                                     threshold=group.expected_count, depends_on=[group.acquire_dependencies[waiter]]))
            end = f'{prefix}.acquire.{waiter}'
            compute(end, resource, 0., (wait, prepared))
            consumptions[end] = (counter, group.expected_count)
            group_ends.append(end)
        end = prefix + '.complete'
        completion_counter = end + '.counter'
        counters.append(dict(counter_id=completion_counter, initial_value=0))
        transactions.append(dict(kind='signal', transaction_id=end, counter_id=completion_counter,
                                 delta=1, depends_on=group_ends))
        terminals.append(end)
    batch = GenericTransactionBatch.model_validate_json(json.dumps(dict(
        kind='generic_transaction_batch', schema_version=1, batch_id='source-bridge-commid',
        timing=binding.timing, counters=counters, transactions=transactions, max_cycles=max_cycles)))
    spec = SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec', schema_version=1,
        spec_id='source-bridge-commid', graph=binding.graph, batch=batch.model_dump(mode='json'))))
    plan = compile_system(spec)
    runtime = BridgeRuntime(plan, consumptions)
    if cancel:
        if cancel[0] not in {transaction['transaction_id'] for transaction in transactions}:
            raise ValueError('unknown cancellation target')
        def cancelling():
            yield runtime.env.timeout(cancel[1])
            runtime.cancel(cancel[0])
        runtime.env.process(cancelling())
    result = runtime.run()
    ledger = []
    for end, (counter, count) in consumptions.items():
        consumed = runtime.consumed.get(counter, 0)
        produced = runtime.bus.counter(counter).value + consumed
        ledger.append(dict(counter=counter, expected=count, produced=produced, consumed=consumed,
                           remaining=produced - consumed, terminal=end))
    if result.status == 'complete' and any(item['remaining'] or item['consumed'] != item['expected'] for item in ledger):
        raise ValueError('completed synchronization has unconsumed releases')
    receipt = dict(schema='t81.bridge.commid.v1', timing_owner=OWNER, native_plan_sha256=plan.plan_sha256,
        formula_sha256=formula.identity_sha256, binding_sha256=binding.identity_sha256,
        domain_sha256=None if domain is None else domain.identity_sha256,
        groups=[group.model_dump(mode='json') for group in groups], ledger=ledger,
        parameters=values, terminals=terminals, reported_unit='reported_ns', seconds_per_unit=1e-9,
        excluded_cost_owners=EXCLUDED, cleanup=runtime.registry.all_released(),
        physical_publishable=False, status=result.status)
    return BridgeExecution(result, runtime, receipt)
