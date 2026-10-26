"""Bounded cleanup primitives independent of instrumentable context methods.

All operations use the one ResourceRegistry's actual requests and leases.
These helpers allocate no resources, reset no counters, and emit no trace.
"""
import math
import simpy
from .quantity_resources import QuantityRegistryMixin


def record_cleanup(context, error):
    if id(error) not in context._cleanup_seen:
        context._cleanup_seen.add(id(error))
        context._cleanup_errors.append(error)


def first_failure(context, error):
    """Store this explicitly observed failure, never an ambient exception chain."""
    if context._first_error is None:
        context._first_error = error
    return context._first_error


def capture_failure(context, error):
    """Called only at an owned execution boundary before finalization."""
    if isinstance(error, simpy.Interrupt):
        return
    if context._aborting:
        record_cleanup(context, error)
    else:
        first_failure(context, error)


def owned_processes(context, processes=None):
    business = list((context._processes if processes is None else processes).values())
    if context.network is not None:
        business.append(context.network.fault_process)
    return tuple(dict.fromkeys(business + list(context._background)))


def settle_owned(context, processes, *, deadline):
    """Consume completion/failure of every process, with time and step bounds."""
    processes = tuple(dict.fromkeys(processes))
    def observe(event):
        if not event.ok:
            event.defused = True
            record_cleanup(context, event.value)
    for process in processes:
        if process.processed:
            if not process.ok:
                record_cleanup(context, process.value)
        else:
            if process.triggered and not process.ok:
                process.defused = True
            process.callbacks.append(observe)
    steps = 0
    limit = max(1000, 100 * (len(processes) + len(context.plan.content.transactions) + 1))
    while any(not process.processed for process in processes):
        next_tick = context.env.peek()
        if not math.isfinite(next_tick) or next_tick > deadline or steps >= limit:
            record_cleanup(context, RuntimeError("E_CLEANUP_BUDGET"))
            return False
        try:
            context.env.step()
        except Exception as exc:
            record_cleanup(context, exc)
        steps += 1
    return True


def dispose_owned(registry):
    """Cancel pending requests before releasing actual ledger-owned grants."""
    registry._closing = True
    errors = []
    resources = [r for e in registry._entries.values() for r in (e.credits, e.serializer) if r is not None]
    for resource in resources:
        for request in tuple(resource.queue):
            try:
                request.cancel()
            except Exception as exc:
                errors.append(exc)
    for resource in resources:
        for request in tuple(resource.users):
            try:
                resource.release(request)
            except Exception as exc:
                errors.append(exc)
    for lease in tuple(registry._quantity_leases.values()):
        try:
            # Bypass higher-level release/observer hooks, not the ownership
            # validation or per-claim accounting of the existing registry.
            QuantityRegistryMixin._drop_quantity_lease(registry, lease, strict=False, notify=False)
        except Exception as exc:
            errors.append(exc)
    for token in tuple(registry._quantity_waiters):
        registry._quantity_waiters.pop(token, None)
    return errors


def reconcile_scheduler(scheduler):
    registry = scheduler.registry
    for key, grant in tuple(scheduler._active_grants.items()):
        for resource_id, lease in tuple(grant.leases.items()):
            if registry._quantity_leases.get(lease.lease_id) is not lease:
                grant.leases.pop(resource_id)
        if grant.serializer is not None:
            resource, request = grant.serializer
            if request not in resource.users and request not in resource.queue:
                grant.serializer = None
        if not grant.leases and grant.serializer is None:
            scheduler._active_grants.pop(key)
    for lease_id, lease in tuple(scheduler.network._held_buffers.items()):
        if registry._quantity_leases.get(lease_id) is not lease:
            scheduler.network._held_buffers.pop(lease_id)


def ownership_clear(context, processes=None):
    """Check actual counts and producers; no status flag is used as proof."""
    registry = context.registry
    if registry._quantity_leases or registry._quantity_waiters:
        return False
    if any(type(p['used']) is not int or p['used'] != 0 for p in registry._quantity_pools.values()):
        return False
    for entry in registry._entries.values():
        for resource in (entry.serializer, entry.credits):
            if resource is not None and (resource.users or resource.queue):
                return False
    if context.storage is not None:
        if context.storage.live or context.storage.accesses or any(q['items'] for q in context.storage.queues.values()):
            return False
    if context.network is not None:
        scheduler = context.network.scheduler
        if any(scheduler.queues.values()) or scheduler._active_grants or context.network._held_buffers:
            return False
    return all(p.processed for p in owned_processes(context, processes))


def cleanup_deadline(context):
    deadline = float(context.env.now) + context.plan.content.max_cycles * 2 + 100.0
    if not math.isfinite(deadline):
        deadline = float(context.env.now)
    valid = [v for v in context._background_due.values()
             if type(v) in (int, float) and math.isfinite(v)]
    return max([deadline] + valid)


def emergency_cleanup(context, processes=None):
    """Second-tier bounded drain and compensation; never call normal phases."""
    context._aborting = True
    context.registry._closing = True
    if context.network is not None:
        context.network.scheduler.closed = True
    processes = context._processes if processes is None else processes
    business = list(processes.values())
    if context.network is not None:
        business.append(context.network.fault_process)
    def interrupt(values):
        for process in tuple(dict.fromkeys(values)):
            if not process.triggered:
                try:
                    process.interrupt()
                except Exception as exc:
                    record_cleanup(context, exc)
    try:
        interrupt(business)
        try:
            settle_owned(context, owned_processes(context, processes), deadline=cleanup_deadline(context))
        except Exception as exc:
            record_cleanup(context, exc)
        pending = [p for p in owned_processes(context, processes) if not p.processed]
        if pending:
            interrupt(pending)
            try:
                settle_owned(context, pending, deadline=float(context.env.now))
            except Exception as exc:
                record_cleanup(context, exc)
    finally:
        # No admission is possible. These operations run even when settling
        # itself fails; they do not call a failed dispose/forget/cancel hook.
        if context.storage is not None:
            # All producers have been stopped; reconcile storage allocations
            # before disposing the shared quantity ledger.
            for _ in range(len(context.storage.live) + 1):
                try:
                    context.storage.cleanup()
                    break
                except Exception as error:
                    record_cleanup(context, error)
        for error in dispose_owned(context.registry):
            record_cleanup(context, error)
        if context.network is not None:
            scheduler = context.network.scheduler
            for queue in scheduler.queues.values():
                for ticket in tuple(queue):
                    queue.remove(ticket)
            try:
                reconcile_scheduler(scheduler)
            except Exception as exc:
                record_cleanup(context, exc)
        context._terminated = True
        context.cleanup_status = "complete" if ownership_clear(context, processes) else "cleanup_incomplete"
        if context.cleanup_status == "cleanup_incomplete":
            record_cleanup(context, RuntimeError("E_CLEANUP_INCOMPLETE"))
    return context.cleanup_status == "complete"


def cleanup_after_failure(context, processes=None):
    """Guard the entire normal cleanup call and independently verify its work."""
    failed = False
    try:
        context.cleanup(processes)
    except Exception as exc:
        failed = True
        record_cleanup(context, exc)
    finally:
        if failed or not ownership_clear(context, processes):
            emergency_cleanup(context, processes)
    return context.cleanup_status
