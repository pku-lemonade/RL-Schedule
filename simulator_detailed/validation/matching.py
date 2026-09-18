"""Explicit identifier mappings and partial-order observable comparison."""

from collections import Counter, defaultdict
from collections.abc import Mapping

from ..configs.schemas.validation import NormalizedObservations
from .data import require


def functional_match(actual: NormalizedObservations, reference: NormalizedObservations,
                     entities: Mapping[str, str], events: Mapping[str, str]) -> None:
    """Map reference IDs to actual IDs; never infer equivalence from names/times."""
    actual_entities = {e.entity_id: e for e in actual.entities}
    reference_entities = {e.entity_id: e for e in reference.entities}
    require(len(set(entities.values())) == len(entities), "entity mapping must be one-to-one")
    for source, target in entities.items():
        require(source in reference_entities and target in actual_entities, "mapping contains unknown entity")
        require(reference_entities[source].role == actual_entities[target].role, "mapping changes entity role")
        owner = reference_entities[source].physical_owner
        if owner is not None:
            require(entities.get(owner) == actual_entities[target].physical_owner, "mapping changes physical resource owner")
    needed = {identity for effect in reference.effects for identity in (effect.destination_id, effect.resource_id)}
    require(needed <= set(entities), "missing explicit effect entity mapping")
    require(bool(reference.effects), "reference has no supported addressed effects")
    def coverage(observations: NormalizedObservations, mapping: Mapping[str, str]) -> dict[tuple[str, str], tuple[tuple[int, int], ...]]:
        deltas: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
        for effect in observations.effects:
            destination, resource = mapping[effect.destination_id], mapping[effect.resource_id]
            changes = deltas[(destination, resource)]
            changes[effect.offset_bytes] += effect.count
            changes[effect.offset_bytes + effect.size_bytes] -= effect.count
        return {identity: tuple(sorted((position, delta) for position, delta in changes.items() if delta)) for identity, changes in deltas.items()}
    expected = coverage(reference, entities)
    observed = coverage(actual, {identity: identity for identity in actual_entities})
    require(expected == observed, "addressed effects differ")
    reference_events = {e.event_id: e for e in reference.events}
    actual_events = {e.event_id: e for e in actual.events}
    needed_events = {i for edge in reference.causal_edges for i in (edge.before, edge.after)}
    needed_events.update(e.visibility_event for e in reference.effects if e.visibility_event is not None)
    require(needed_events <= set(events), "missing explicit causal/visibility event mapping")
    require(len(set(events.values())) == len(events), "event mapping must be one-to-one")
    for source, target in events.items():
        require(source in reference_events and target in actual_events, "mapping contains unknown event")
        left, right = reference_events[source], actual_events[target]
        require(left.action == right.action and entities.get(left.subject_id) == right.subject_id, "mapped event semantics differ")
        for counter in left.counters:
            require(counter in right.counters, "mapped event counters differ")
    successors: dict[str, set[str]] = defaultdict(set)
    for edge in actual.causal_edges:
        successors[edge.before].add(edge.after)
    def reachable(start: str) -> set[str]:
        pending, visited = [start], set[str]()
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(successors[current])
        return visited

    for edge in reference.causal_edges:
        require(events[edge.after] in reachable(events[edge.before]), "required causal relation is absent")
    for expected_effect in reference.effects:
        if expected_effect.visibility_event is None:
            continue
        visible = events[expected_effect.visibility_event]
        for effect in actual.effects:
            if ((effect.destination_id, effect.resource_id) == (entities[expected_effect.destination_id], entities[expected_effect.resource_id])
                    and effect.offset_bytes < expected_effect.offset_bytes + expected_effect.size_bytes
                    and expected_effect.offset_bytes < effect.offset_bytes + effect.size_bytes):
                require(effect.visibility_event is not None and visible in reachable(effect.visibility_event), "effect not published before mapped visibility event")
