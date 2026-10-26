"""M01/M02 execution reuses RuntimeContext ownership and transport."""
from dataclasses import dataclass
from ..runtime_context import RuntimeContext
from .lowering import compile_network
from .receipt import ResourceObserver, build_receipt

@dataclass(frozen=True)
class NetworkExecution:
    compiled: object
    runtime: object
    result: object
    receipt: dict
    error: str | None

def execute_network(workload,binding,hardware,*,production=True,max_cycles=100000.0,cancel=None,inject_failure=False):
    compiled=compile_network(workload,binding,hardware,production=production,max_cycles=max_cycles)
    runtime=RuntimeContext(compiled.plan);observer=ResourceObserver(runtime)
    if cancel:
        target,time=cancel
        valid={t.transaction_id for t in compiled.plan.content.transactions}|{'network.whole'}
        if target not in valid:raise ValueError('unknown cancellation target')
        def cancelling():
            yield runtime.env.timeout(time)
            runtime.cancel(target)
        runtime.env.process(cancelling())
    if inject_failure:
        original=runtime._emit
        def emit(action,transaction_id=None,resource_id=None,detail=None):
            if action=='fifo_event' and detail and 'packet_forward' in detail:
                raise RuntimeError('T81_INJECTED_PACKET_FAILURE')
            return original(action,transaction_id,resource_id,detail)
        runtime._emit=emit
    result=None;error=None
    try:result=runtime.run()
    except RuntimeError as exc:error=str(exc)
    receipt=build_receipt(compiled,runtime,result,observer,error)
    return NetworkExecution(compiled,runtime,result,receipt,error)
