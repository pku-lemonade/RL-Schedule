"""Standalone public API replay used by the hash-seed/block-order matrix."""
import io
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'))
import causal_full_analysis as full
import causal_full_fixtures as fixtures
import design_space as api


if __name__ == '__main__':
    model = api.DesignSpace(fixtures.model())
    request = fixtures.request(model)
    producer = full._comparison_blocks
    def scheduled(*args, **kwargs):
        return producer(*args, **dict(kwargs, reverse=sys.argv[2] == 'reverse'))
    with patch.object(full, '_comparison_blocks', scheduled):
        model.evaluate_to(request, sys.stdout.buffer, block_size=int(sys.argv[1]))
