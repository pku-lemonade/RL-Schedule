"""Logical flows lower only through the existing GenericTransactionBatch/SystemSpec."""
from dataclasses import dataclass
import json
from .binding import NetworkWorkload, SuppliedBinding
from .hardware import HardwareParameterSet
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system

@dataclass(frozen=True)
class CompiledNetwork:
    workload: NetworkWorkload
    binding: SuppliedBinding
    hardware: HardwareParameterSet
    spec: SystemSpec
    plan: object
    native_members: dict
    effective_timing: list

def compile_network(workload, binding, hardware, *, max_cycles=100000.0, production=True):
    # Revalidate caller records, including nested dictionary hashes, at ingress.
    workload=NetworkWorkload.model_validate_json(workload.model_dump_json())
    binding=SuppliedBinding.model_validate_json(binding.model_dump_json())
    hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json())
    if production and (binding.fixture_only or workload.fixture_only):
        raise ValueError('production path forbids fixture-only workload/binding')
    transactions=[]; counters=[]; members={}
    routes={(r['network_id'],r['source'],r['destination']):r['link_ids'] for r in binding.graph.get('static_routes',[])}
    effective=binding.effective_timing(hardware)
    for flow in workload.flows:
        source=binding.resolve(flow.source_role); ids=[]; group_network=None; path_list=[]
        for i,dest_role in enumerate(flow.destination_roles):
            dest=binding.resolve(dest_role)
            matches=[(n,path) for (n,s,d),path in routes.items() if s==source and d==dest]
            if len(matches)!=1:raise ValueError('supplied binding must contain one exact route for each flow')
            n,path=matches[0]
            if group_network and n!=group_network:raise ValueError('multicast endpoints must share network')
            group_network=n;path_list.append(path)
            tid=f'{flow.flow_id}.member.{i:02d}';ids.append(tid)
            tx=dict(transaction_id=tid,kind='transfer',network_id=n,source=source,destination=dest,
                payload_bytes=flow.payload_bytes,depends_on=list(flow.depends_on),start_cycles=flow.start_cycles)
            if flow.primitive=='masked_multicast':tx['multicast_group']=flow.flow_id
            transactions.append(tx)
        if flow.primitive=='masked_multicast' and len(path_list)>1:
            if len({p[0] for p in path_list})!=1:raise ValueError('multicast binding has no common physical prefix')
        counter=flow.flow_id+'.completion';counters.append(dict(counter_id=counter,initial_value=0))
        transactions.append(dict(kind='signal',transaction_id=flow.flow_id,counter_id=counter,delta=1,
            depends_on=ids,start_cycles=0))
        members[flow.flow_id]=ids
    batch_raw=dict(kind='generic_transaction_batch',schema_version=1,batch_id=workload.workload_id,
        timing=effective,counters=counters,transactions=transactions,max_cycles=max_cycles,
        operation_envelopes=[dict(operation_id='network.whole',member_transactions=list(members),
            boundary_ref='first-eligible-flow-to-all-destinations-complete')])
    batch=GenericTransactionBatch.model_validate_json(json.dumps(batch_raw))
    raw=dict(kind='system_spec',schema_version=1,spec_id=workload.workload_id,
        graph=binding.graph,batch=batch.model_dump(mode='json'))
    spec=SystemSpec.model_validate_json(json.dumps(raw))
    plan=compile_system(spec)
    return CompiledNetwork(workload,binding,hardware,spec,plan,members,effective)
