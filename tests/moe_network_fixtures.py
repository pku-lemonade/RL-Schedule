"""Explicitly fixture-only supplied bindings; no runtime-created topology."""
from copy import deepcopy
from moe_fifo_fixtures import multicast_plan, _graph, path_timings
from simulator_detailed.moe_model.binding import *
from simulator_detailed.moe_model.hardware import *


def fixture(multicast=True,payload=40):
    if multicast:
        base=multicast_plan()
        # Recover the caller-supplied SystemSpec from the compile fixture.
        graph=dict(kind='generic_system_graph',schema_version=1,system_id='t81-fixture',
          nodes=[dict(node_id=n,x=i,y=0,role='compute') for i,n in enumerate(('s','branch','d0','d1','other-s','other-d','other-b'))],
          networks=[dict(network_id='noc')],
          ports=[dict(port_id=p,node_id=n,network_id='noc',kind='local' if p=='local' else 'network')
                 for n in ('s','branch','d0','d1','other-s','other-d','other-b') for p in ('out','in','local')]
              +[dict(port_id='out-1',node_id='branch',network_id='noc',kind='network')],
          links=[dict(link_id='prefix',network_id='noc',src_node='s',src_port='out',dst_node='branch',dst_port='in'),
                 dict(link_id='leaf-0',network_id='noc',src_node='branch',src_port='out',dst_node='d0',dst_port='in'),
                 dict(link_id='leaf-1',network_id='noc',src_node='branch',src_port='out-1',dst_node='d1',dst_port='in'),
                 dict(link_id='solo-in',network_id='noc',src_node='other-s',src_port='out',dst_node='other-b',dst_port='in'),
                 dict(link_id='solo-out',network_id='noc',src_node='other-b',src_port='out',dst_node='other-d',dst_port='in')],
          execution_units=[dict(unit_id=e,node_id=n,network_id='noc',port_id='local')
                           for e,n in [('src','s'),('dst0','d0'),('dst1','d1'),('solo-src','other-s'),('solo-dst','other-d')]],
          dma_endpoints=[],memory_resources=[],static_routes=[
              dict(network_id='noc',source='src',destination=f'dst{i}',link_ids=['prefix',f'leaf-{i}']) for i in range(2)]
              +[dict(network_id='noc',source='solo-src',destination='solo-dst',link_ids=['solo-in','solo-out'])])
        root=base.content.transactions[0].hops[0].timing.model_dump(mode='json')
        root['issue_cycles']=4
        leaves=[deepcopy(base.content.transactions[i].hops[1].timing.model_dump(mode='json')) for i in range(2)]
        for t in leaves:t['issue_cycles']=4
        solo_in=dict(root,input_resource_id='solo-input',output_resource_id='solo-unused-out',channel_resource_id='solo-unused-ch')
        solo_out=dict(leaves[0],output_resource_id='solo-output',channel_resource_id='solo-channel')
        timing=[dict(network_id='noc',link=root,overrides=[dict(link_id=f'leaf-{i}',timing=t) for i,t in enumerate(leaves)]+
            [dict(link_id='solo-in',timing=solo_in),dict(link_id='solo-out',timing=solo_out)])]
        endpoints=tuple(EndpointBinding(role_id=r,endpoint_id=e,kind=k) for r,e,k in
            [('gm0','src','gm_rdma'),('pe0','dst0','pe'),('pe1','dst1','pe'),('gm1','solo-src','gm_rdma'),('pe2','solo-dst','pe')])
        flows=(LogicalFlow(flow_id='mc',source_role='gm0',destination_roles=('pe0','pe1'),payload_bytes=payload,primitive='masked_multicast'),
               LogicalFlow(flow_id='solo',source_role='gm1',destination_roles=('pe2',),payload_bytes=payload,primitive='point_to_point'))
        target='prefix'
    else:
        graph=_graph([2]);ts=path_timings([2],capacity=2)
        for t in ts.values():t['issue_cycles']=4
        timing=[dict(network_id='noc',link=ts['f0h0'],overrides=[dict(link_id='f0h1',timing=ts['f0h1'])])]
        endpoints=(EndpointBinding(role_id='gm0',endpoint_id='src-0',kind='gm_rdma'),EndpointBinding(role_id='pe0',endpoint_id='dst-0',kind='pe'))
        flows=(LogicalFlow(flow_id='p2p',source_role='gm0',destination_roles=('pe0',),payload_bytes=payload,primitive='point_to_point'),)
        target='f0h1'
    path='data_noc.links.'+target+'.bytes_per_cycle'
    binding=freeze_binding(binding_id='fixture',fixture_only=True,graph=graph,timing=tuple(timing),endpoints=endpoints,
        parameters=(ParameterBinding(parameter_path=path,network_id='noc',link_ids=(target,),allowed_component='M02' if multicast else 'M01'),),
        source_refs=('t81_phase1_fixtures.py explicitly synthetic',),calibration_sha256='a'*64)
    hp=HardwareParameter(path=path,resource_id='noc/'+target,component='M02' if multicast else 'M01',
        quantity=ParameterValue(value=2.0,unit='byte/cycle',source_ref='fixture-only',calibration_status='uncalibrated',
            applicability=ApplicabilityDomain(description='CPU fixture only'),minimum=0.01))
    hardware=freeze_hardware_parameters('fixture','cycles',(hp,),('fixture-only',))
    work=freeze_network_workload(workload_id='fixture-network',fixture_only=True,flows=flows,source_refs=('fixture-only',))
    return work,binding,hardware
