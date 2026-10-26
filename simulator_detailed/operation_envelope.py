"""Operation phases share RuntimeContext events; transport stays local and unchanged."""
import json
import simpy

def run_operation_envelope(context,envelope):
    local={p.phase_id:context.env.event()for p in envelope.phases};rows=[];children=[];start=None
    def phase(p):
        try:
            dependencies=[]
            for key in p.depends_on:dependencies.append(local[key]if key in local else context._done[key])
            if dependencies:yield context.env.all_of(dependencies)
            target=start+p.start_offset_cycles
            if target>context.env.now:yield context.env.timeout(target-context.env.now)
            began=float(context.env.now)
            yield context.env.timeout(p.duration_cycles)
            rows.append(dict(phase_id=p.phase_id,start_cycles=began,end_cycles=float(context.env.now),duration_cycles=p.duration_cycles,
                             depends_on=list(p.depends_on),evidence_ref=p.evidence_ref,coverage=p.coverage))
            local[p.phase_id].succeed()
        except simpy.Interrupt:
            return
    try:
        yield from context._start(envelope.depends_on,envelope.start_cycles)
        start=float(context.env.now)
        for p in envelope.phases:children.append(context.env.process(phase(p)))
        waits=[context._done[k]for k in envelope.member_transactions]+[local[k]for k in(envelope.completion_phases or tuple(local))]
        if waits:yield context.env.all_of(waits)
        # All nonterminal phase state also drains before claiming envelope completion.
        if children:yield context.env.all_of(children)
        end=float(context.env.now);members=[]
        for key in envelope.member_transactions:
            span=context._spans.get(key)
            if span is None or span.status!='complete':raise ValueError('ENVELOPE_MEMBER_INCOMPLETE')
            if span.start_cycles<start:raise ValueError('ENVELOPE_MEMBER_BEFORE_START')
            events=[e for e in getattr(context,'_fifo_events',[])if e['transaction_id']==key]
            admits=[e['time_cycles']for e in events if e['action']=='fifo_admit'];arrivals=[e['time_cycles']for e in events if e['action']=='packet_delivered']
            members.append(dict(transaction_id=key,start_cycles=span.start_cycles,end_cycles=span.end_cycles,
                network_admission_cycles=min(admits)if admits else None,network_completion_cycles=max(arrivals)if arrivals else None))
        context._operation_envelopes[envelope.operation_id]=dict(operation_id=envelope.operation_id,status='complete',start_cycles=start,end_cycles=end,duration_cycles=end-start,
            phases=rows,members=members,boundary_ref=envelope.boundary_ref,network_counting='actual events once; phase dependency/max composition',
            network_coverage='preservation-only'if any(p.coverage=='preserved_whole_operation'for p in envelope.phases)else'live_event_composition')
        context._done[envelope.operation_id].succeed()
    except simpy.Interrupt:
        pass
    finally:
        for p in children:
            if p.is_alive:p.interrupt()
        if children:yield context.env.all_of(children)
        if envelope.operation_id not in context._operation_envelopes:
            context._operation_envelopes[envelope.operation_id]=dict(operation_id=envelope.operation_id,status='incomplete',start_cycles=start,end_cycles=None,phases=rows,members=[],boundary_ref=envelope.boundary_ref)


def validate_operation_envelopes(result,content):
    declared={e.operation_id:e for e in content.operation_envelopes}
    actual={e['operation_id']:e for e in result.operation_envelopes}
    if len(actual)!=len(result.operation_envelopes)or set(actual)!=set(declared):raise ValueError('ENVELOPE_RECEIPT_COVERAGE')
    spans={s.transaction_id:s for s in result.transactions};completed={}
    for key,e in declared.items():
        r=actual[key]
        if r['status']!='complete':
            if r.get('end_cycles')is not None:raise ValueError('ENVELOPE_INCOMPLETE_PUBLISHED')
            continue
        if r['boundary_ref']!=e.boundary_ref or r['start_cycles']<e.start_cycles:raise ValueError('ENVELOPE_BOUNDARY_RECEIPT')
        phases={p['phase_id']:p for p in r['phases']}
        if len(phases)!=len(r['phases'])or set(phases)!={p.phase_id for p in e.phases}:raise ValueError('ENVELOPE_PHASE_COVERAGE')
        ends=[]
        for member in e.member_transactions:
            s=spans[member]
            if s.status!='complete':raise ValueError('ENVELOPE_MEMBER_INCOMPLETE_RECEIPT')
            ends.append(s.end_cycles)
        for p in e.phases:
            q=phases[p.phase_id]
            if q['end_cycles']!=q['start_cycles']+p.duration_cycles or q['start_cycles']<r['start_cycles']+p.start_offset_cycles:raise ValueError('ENVELOPE_PHASE_DURATION')
            for dep in p.depends_on:
                end=phases[dep]['end_cycles']if dep in phases else spans[dep].end_cycles
                if end is None or q['start_cycles']<end:raise ValueError('ENVELOPE_PHASE_CAUSALITY')
            ends.append(q['end_cycles'])
        if r['end_cycles']!=max(ends) or r['duration_cycles']!=r['end_cycles']-r['start_cycles']:raise ValueError('ENVELOPE_COMPLETION_RECEIPT')
        completed[key]=(r['end_cycles'],None)
    return completed
