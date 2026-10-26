"""Opt-in MoE FIFO/envelope execution on the existing context and registry."""
from __future__ import annotations

import math
from typing import Literal
import simpy

from .configs.schemas.generic_transactions import (
    GenericErrorRecord, GenericProgramSpan, GenericResourceUsage, GenericResultSpan,
    GenericSimulationResult, GenericTransactionSpan, GenericWholeOperationSpan,
)
from .configs.schemas.system_spec import PlanTransfer, PlanWholeOperation
from .runtime_cleanup import (
    record_cleanup, first_failure, capture_failure, settle_owned, ownership_clear,
    cleanup_deadline, emergency_cleanup, cleanup_after_failure,
)


class MoeRuntimeExtensions:
    def _initialize_moe_extensions(self):
        self.storage = None
        self.network = None
        self._processes = {}
        self._cancel_requested = set()
        self._background_due = {}
        self._background_errors = []
        self._failure_event = self.env.event()
        self._cleanup_errors = []
        self._cleanup_seen = set()
        self._aborting = False
        self._terminated = False
        self._first_error = None
        self.cleanup_status = "not_started"
        self._operation_envelopes = {}

    def _moe_extensions_enabled(self):
        return bool(self.plan.content.operation_envelopes or self.registry._quantity_pools or any(
            isinstance(transaction, PlanTransfer) and transaction.hops
            and getattr(transaction.hops[0].timing, "mode", None) == "latency_fifo"
            for transaction in self.plan.content.transactions
        ))

    def _owned_process(self, generator):
        """Capture an unhandled owned-generator error before SimPy propagation."""
        try:
            return (yield from generator)
        except Exception as exc:
            capture_failure(self, exc)
            raise

    def _request_cancel(self, transaction_id, process, cause=None):
        if process.triggered or transaction_id in self._cancel_requested:
            return
        self._cancel_requested.add(transaction_id)
        self._emit("cancel", transaction_id)
        process.interrupt(cause)

    def _cancel_moe_processes(self, processes: dict[str, simpy.Process]) -> None:
        """A failed cancellation trace cannot prevent the actual interrupt."""
        errors = []
        for transaction_id, process in tuple(processes.items()):
            if not process.triggered:
                try:
                    if transaction_id not in self._cancel_requested:
                        self._cancel_requested.add(transaction_id)
                        self._emit("cancel", transaction_id)
                except Exception as exc:
                    capture_failure(self, exc)
                    errors.append(exc)
                finally:
                    try:
                        process.interrupt()
                    except Exception as exc:
                        errors.append(exc)
        if errors:
            for extra in errors[1:]:
                errors[0].add_note(f"additional cancellation failure: {extra!r}")
            raise errors[0]

    def _note_cleanup(self, error):
        record_cleanup(self, error)

    def spawn_background(self, generator, *, deadline):
        """Register a return process before its owner transfers the lease."""
        if (self._aborting or type(deadline) not in (int, float)
                or not math.isfinite(deadline) or deadline < self.env.now):
            generator.close()
            raise ValueError("E_BACKGROUND_TIME_OR_CLOSED")
        process = self.env.process(self._owned_process(generator))
        self._background.append(process)
        self._background_due[process] = deadline
        def observe(event):
            if not event.ok:
                event.defused = True
                capture_failure(self, event.value)
                self._background_errors.append(event.value)
                if not self._failure_event.triggered:
                    self._failure_event.succeed(event.value)
        process.callbacks.append(observe)
        return process

    def _settle_cleanup(self, processes, *, deadline):
        return settle_owned(self, processes, deadline=deadline)

    def _drain_moe_background(self) -> None:
        """Normal scheduling is unchanged; failure is never returned as success."""
        if self._background_errors:
            raise self._background_errors[0]
        if self._background:
            self.env.run(until=self.env.all_of(tuple(self._background)))
        if self._background_errors:
            raise self._background_errors[0]

    def cleanup(self, processes=None):
        """Guard every normal phase, then prove or independently compensate."""
        processes = self._processes if processes is None else processes
        if self._terminated and ownership_clear(self, processes):
            return tuple(self._cleanup_errors)
        self._aborting = True
        self.registry._closing = True
        failed = False
        def attempt(function, *args, **kwargs):
            nonlocal failed
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                failed = True
                record_cleanup(self, exc)
                return None
        attempt(self._cancel_moe_processes, processes)
        business = list(processes.values())
        deadline = cleanup_deadline(self)
        attempt(self._settle_cleanup, business, deadline=deadline)
        attempt(self._settle_cleanup, self._background, deadline=deadline)
        pending = [process for process in business + self._background if not process.triggered]
        for process in pending:
            attempt(process.interrupt)
        if pending:
            attempt(self._settle_cleanup, pending, deadline=float(self.env.now))
        errors = attempt(self.registry.dispose)
        for error in errors or ():
            record_cleanup(self, error)
        self._terminated = True
        if failed or not ownership_clear(self, processes):
            emergency_cleanup(self, processes)
        else:
            self.cleanup_status = "complete"
        return tuple(self._cleanup_errors)

    def _abort_moe(self, processes: dict[str, simpy.Process], exc: Exception) -> None:
        """Store the first cause before invoking any cleanup extension point."""
        root = first_failure(self, exc)
        try:
            cleanup_after_failure(self, processes)
        except Exception as extra:
            record_cleanup(self, extra)
            try:
                emergency_cleanup(self, processes)
            except Exception as final_error:
                record_cleanup(self, final_error)
                self.cleanup_status = "cleanup_incomplete"
        for error in self._cleanup_errors:
            if error is not root:
                root.add_note(f"cleanup also observed: {error!r}")
        suffix = " [cleanup_incomplete]" if self.cleanup_status != "complete" else ""
        raise RuntimeError(
            f"simulation aborted at {float(self.env.now)} cycles: {root}{suffix}"
        ) from root

    def cancel(self, transaction_id):
        process = self._processes.get(transaction_id)
        if process is not None:
            self._request_cancel(transaction_id, process, "cancelled")

    def _run_moe_extensions(self) -> GenericSimulationResult:
        if self._terminated:
            raise RuntimeError("E_RUNTIME_CLOSED")
        op_ids = frozenset(
            op.transaction_id
            for program in self.plan.content.programs
            for op in program.ops
        )
        processes: dict[str, simpy.Process] = {}
        self._processes = processes
        for transaction in self.plan.content.transactions:
            self._done[transaction.transaction_id] = self.env.event()
            if transaction.transaction_id in self._terminal:
                continue
            if transaction.transaction_id in op_ids:
                continue
            processes[transaction.transaction_id] = self.env.process(
                self._owned_process(self._dispatch(transaction))
            )
        from .operation_envelope import run_operation_envelope
        for envelope in self.plan.content.operation_envelopes:
            self._done[envelope.operation_id] = self.env.event()
        for envelope in self.plan.content.operation_envelopes:
            processes[envelope.operation_id] = self.env.process(
                self._owned_process(run_operation_envelope(self, envelope))
            )
        for program in self.plan.content.programs:
            processes[program.program_id] = self.env.process(
                self._owned_process(self._program(program, processes))
            )
        pending = tuple(
            event
            for transaction_id, event in self._done.items()
            if transaction_id not in self._terminal
        )
        drained = self.env.all_of(pending)
        try:
            self.env.run(
                until=simpy.AnyOf(
                    self.env, (drained, self.env.timeout(self.plan.content.max_cycles), self._failure_event)
                )
            )
            if self._background_errors:
                raise self._background_errors[0]
            if not drained.triggered:
                self._cancel_moe_processes(processes)
                self._await_processes(processes)
            self._drain_moe_background()
        except Exception as exc:
            self._abort_moe(processes, exc)
        if not self.registry.all_released():
            self._abort_moe(processes, RuntimeError(
                "resources still held or queued after cleanup and drain; "
                "refusing to report a result"
            ))

        try:
            return self._build_moe_result()
        except Exception as exc:
            root = first_failure(self, exc)
            try:
                cleanup_after_failure(self, processes)
            except Exception as extra:
                record_cleanup(self, extra)
                try:
                    emergency_cleanup(self, processes)
                except Exception as final_error:
                    record_cleanup(self, final_error)
                    self.cleanup_status = "cleanup_incomplete"
            for error in self._cleanup_errors:
                if error is not root:
                    root.add_note(f"cleanup also observed: {error!r}")
            if self.cleanup_status != "complete":
                root.add_note("cleanup_incomplete")
            raise root

    def _build_moe_result(self) -> GenericSimulationResult:
        spans: list[GenericResultSpan] = []
        errors: list[GenericErrorRecord] = []
        for transaction in self.plan.content.transactions:
            terminal_reason = self._terminal.get(transaction.transaction_id)
            if terminal_reason is not None and isinstance(transaction, PlanTransfer):
                spans.append(GenericTransactionSpan(
                    transaction_id=transaction.transaction_id,
                    kind=transaction.kind,
                    status="incomplete",
                    reason=terminal_reason,
                    start_cycles=None,
                    end_cycles=None,
                ))
                errors.append(GenericErrorRecord(
                    transaction_id=transaction.transaction_id,
                    code=terminal_reason,
                    message=self._terminal_message(transaction, terminal_reason),
                ))
                continue
            span = self._spans.get(transaction.transaction_id)
            if span is not None:
                spans.append(span)
                continue
            unsatisfied = any(
                dependency in self._terminal or self._spans.get(dependency) is None
                for dependency in transaction.depends_on
            )
            reason: Literal["cycle_limit", "dependency_unsatisfied"] = (
                "dependency_unsatisfied" if unsatisfied else "cycle_limit"
            )
            if isinstance(transaction, PlanWholeOperation):
                spans.append(GenericWholeOperationSpan(
                    transaction_id=transaction.transaction_id, kind="whole_operation",
                    status="incomplete", reason=reason, start_cycles=None, end_cycles=None,
                    operation_cost=transaction.operation_cost,
                ))
            else:
                spans.append(GenericTransactionSpan(
                    transaction_id=transaction.transaction_id,
                    kind=transaction.kind,
                    status="incomplete",
                    reason=reason,
                    start_cycles=None,
                    end_cycles=None,
                ))
            errors.append(GenericErrorRecord(
                transaction_id=transaction.transaction_id,
                code=reason,
                message=self._incomplete_message(transaction, reason),
            ))
        span_by_id = {span.transaction_id: span for span in spans}
        program_spans: list[GenericProgramSpan] = []
        for program in self.plan.content.programs:
            op_spans = [span_by_id[op.transaction_id] for op in program.ops]
            starts = [
                span.start_cycles
                for span in op_spans
                if span.start_cycles is not None
            ]
            if all(span.status == "complete" for span in op_spans):
                ends = [
                    span.end_cycles
                    for span in op_spans
                    if span.end_cycles is not None
                ]
                program_spans.append(GenericProgramSpan(
                    program_id=program.program_id,
                    unit_id=program.unit_id,
                    status="complete",
                    reason="completed",
                    start_cycles=min(starts),
                    end_cycles=max(ends),
                ))
            else:
                stopped = next(
                    span for span in op_spans if span.status == "incomplete"
                )
                program_spans.append(GenericProgramSpan(
                    program_id=program.program_id,
                    unit_id=program.unit_id,
                    status="incomplete",
                    reason=stopped.reason,
                    start_cycles=min(starts) if starts else None,
                    end_cycles=None,
                ))
        completed = [span.end_cycles for span in spans if span.end_cycles is not None]
        envelopes = tuple(
            self._operation_envelopes.get(envelope.operation_id, dict(
                operation_id=envelope.operation_id, status="incomplete",
                start_cycles=None, end_cycles=None, phases=[], members=[],
            ))
            for envelope in self.plan.content.operation_envelopes
        )
        completed.extend(envelope["end_cycles"] for envelope in envelopes if envelope.get("end_cycles") is not None)
        completion = max(completed) if completed else None
        complete = all(span.status == "complete" for span in spans) and all(envelope["status"] == "complete" for envelope in envelopes)

        resources = tuple(
            GenericResourceUsage(
                resource_id=entry.resource_id,
                kind=entry.kind,
                service_count=entry.service_count,
                busy_cycles=entry.busy_cycles,
                queue_wait_cycles=entry.queue_wait_cycles,
                utilization=(
                    entry.busy_cycles / completion
                    if completion is not None and completion > 0
                    else 0.0
                ),
            )
            for entry in self.registry.entries()
        )
        result = GenericSimulationResult(
            batch_id=self.plan.content.spec_id,
            system_id=self.plan.content.system_id,
            status="complete" if complete else "incomplete",
            reason="drained" if complete else "transactions_incomplete",
            completion_cycles=completion,
            graph_sha256=self.plan.content.graph_sha256,
            batch_sha256=self.plan.content.batch_sha256,
            transactions=tuple(spans),
            programs=tuple(program_spans),
            resources=resources,
            counters=self.bus.counters(),
            errors=tuple(errors),
            trace=tuple(self._trace),
            operation_envelopes=envelopes,
            contention=self.contention.summary() if getattr(self, "contention", None) is not None else None,
            plan_sha256=self.plan.plan_sha256,
            execution=(
                "generic_operation_service" if all(span.kind == "whole_operation" for span in spans)
                else "generic_mixed_service" if any(span.kind == "whole_operation" for span in spans)
                else "generic_packet_transport"
            ),
        )

        if self.plan.content.operation_envelopes:
            from .operation_envelope import validate_operation_envelopes
            validate_operation_envelopes(result, self.plan.content)
        return result
