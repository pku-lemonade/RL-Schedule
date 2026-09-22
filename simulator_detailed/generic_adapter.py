"""Public adapter boundary for external/private inputs.

External code converts its own inputs into the neutral public documents
(`generic_system_graph` and `generic_transaction_batch`). The boundary
re-validates returned documents through a dump/parse round trip, so objects
built with validation-bypassing constructors cannot leak through. Phase 1 has
no dynamic discovery, no dynamic imports, no shell evaluation and no network
access: private launch scripts import this library and call adapters directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .configs.schemas.generic_graph import FORBIDDEN_DEVICE_TOKENS, GenericSystemGraph
from .configs.schemas.generic_transactions import (
    GenericSimulationResult,
    GenericTransactionBatch,
)
from .generic_graph import topology_from_generic
from .generic_runtime import run_generic_batch


class GenericInputAdapter(ABC):
    """Neutral conversion contract between private inputs and public documents."""

    @abstractmethod
    def load_system_graph(self) -> GenericSystemGraph:
        """Return the converted system graph; the boundary revalidates it."""

    @abstractmethod
    def load_transactions(self) -> GenericTransactionBatch:
        """Return the converted transaction batch; the boundary revalidates it."""


def run_generic_adapter(adapter: GenericInputAdapter) -> GenericSimulationResult:
    """Revalidate both documents, compile the graph and execute the batch."""
    graph = GenericSystemGraph.model_validate(
        adapter.load_system_graph().model_dump(mode="json", round_trip=True)
    )
    batch = GenericTransactionBatch.model_validate(
        adapter.load_transactions().model_dump(mode="json", round_trip=True)
    )
    return run_generic_batch(topology_from_generic(graph), batch)


@dataclass(frozen=True)
class TokenFinding:
    """One forbidden device/vendor/private token sighting in a public file."""

    path: str
    token: str


def scan_forbidden_tokens(
    paths: Iterable[str | Path],
    tokens: tuple[str, ...] = FORBIDDEN_DEVICE_TOKENS,
) -> tuple[TokenFinding, ...]:
    """Case-insensitive substring scan; a finding names its file and token."""
    findings: list[TokenFinding] = []
    for entry in paths:
        path = Path(entry)
        text = path.read_text().lower()
        for token in tokens:
            if token.lower() in text:
                findings.append(TokenFinding(str(path), token))
    return tuple(sorted(findings, key=lambda finding: (finding.path, finding.token)))
