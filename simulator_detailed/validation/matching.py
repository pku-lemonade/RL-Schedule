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
    needed = {identity for effect in reference.effects for identity in (effect.destination_id, effect.resource_id)}
    require(needed <= set(entities), "missing explicit effect entity mapping")
    require(bool(reference.effects), "reference has no supported addressed effects")
    expected = Counter((entities[e.destination_id], entities[e.resource_id], e.offset_bytes, e.size_bytes, e.count) for e in reference.effects)
    observed = Counter((e.destination_id, e.resource_id, e.offset_bytes, e.size_bytes, e.count) for e in actual.effects)
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
    successors: dict[str, set[str]] = defaultdict(set)
    for edge in actual.causal_edges:
        successors[edge.before].add(edge.after)
    for edge in reference.causal_edges:
        pending, visited = [events[edge.before]], set[str]()
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(successors[current])
        require(events[edge.after] in visited, "required causal relation is absent")
