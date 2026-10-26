"""Generic whole-operation completion over phase and transaction events."""
from typing import Literal
from pydantic import model_validator
from .topology import GraphRecord,Cycles
from .generic_graph import NeutralId

class OperationPhase(GraphRecord):
    phase_id: NeutralId
    duration_cycles: Cycles
    start_offset_cycles: Cycles = 0
    depends_on: tuple[NeutralId,...] = ()
    evidence_ref: NeutralId
    coverage: Literal['non_network','preserved_whole_operation'] = 'non_network'

class OperationEnvelope(GraphRecord):
    operation_id: NeutralId
    start_cycles: Cycles = 0
    depends_on: tuple[NeutralId,...] = ()
    member_transactions: tuple[NeutralId,...] = ()
    phases: tuple[OperationPhase,...] = ()
    completion_phases: tuple[NeutralId,...] = ()
    boundary_ref: NeutralId
    @model_validator(mode='after')
    def references(self):
        ids=[p.phase_id for p in self.phases]
        if len(ids)!=len(set(ids))or len(self.member_transactions)!=len(set(self.member_transactions)):raise ValueError('ENVELOPE_DUPLICATE_MEMBER')
        if not self.member_transactions and not self.phases:raise ValueError('ENVELOPE_EMPTY')
        if set(ids)&(set(self.member_transactions)|{self.operation_id}):raise ValueError('ENVELOPE_NAMESPACE_COLLISION')
        known=set(ids)|set(self.member_transactions)
        if any(k not in known for k in self.completion_phases):raise ValueError('ENVELOPE_COMPLETION_PHASE')
        for p in self.phases:
            if any(k not in known or k==p.phase_id for k in p.depends_on):raise ValueError('ENVELOPE_PHASE_DEPENDENCY')
        pending={p.phase_id:set(p.depends_on)-set(self.member_transactions)for p in self.phases};done=set()
        while pending:
            ready=[k for k,v in pending.items()if v<=done]
            if not ready:raise ValueError('ENVELOPE_PHASE_CYCLE')
            done.update(ready)
            for k in ready:del pending[k]
        covered=[p for p in self.phases if p.coverage=='preserved_whole_operation']
        if covered and(len(covered)!=1 or any(p.depends_on for p in self.phases)):raise ValueError('ENVELOPE_DOUBLE_NETWORK_COVERAGE')
        return self


def validate_envelope_dependencies(transactions,envelopes):
    """Validate global events separately from each envelope-local phase DAG."""
    if not envelopes:return
    declared={t.transaction_id for t in transactions}|{e.operation_id for e in envelopes}
    for e in envelopes:
        if {p.phase_id for p in e.phases}&declared:raise ValueError('ENVELOPE_NAMESPACE_COLLISION')
        if any(k not in declared for k in e.depends_on):raise ValueError('ENVELOPE_DEPENDENCY_REFERENCE')
    incoming={t.transaction_id:set(t.depends_on) for t in transactions}
    incoming.update({e.operation_id:set(e.depends_on)|set(e.member_transactions)for e in envelopes})
    done=set()
    while incoming:
        ready=[k for k,deps in incoming.items() if deps<=done]
        if not ready:raise ValueError('ENVELOPE_MEMBER_DEPENDENCY_cycle')
        done.update(ready)
        for k in ready:del incoming[k]
