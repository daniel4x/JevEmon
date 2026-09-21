"""Live-ROM journey smoke test: walk from the Pallet Town checkpoint with no API calls.

Needs the ROM, the vendored PokéBot Gen3 adapter, and mGBA — run locally after
`scripts/setup.py`, not in CI.
"""
from jevemon import engine
from jevemon.checks import check_journey
from jevemon.config import validate_config
from jevemon.runner import Session


def test_journey_reaches_finished_or_stuck():
    engine.initialise()
    check_journey(Session, validate_config)
