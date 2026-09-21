"""validate_config checks."""
import json

import pytest

from jevemon.config import validate_config
from jevemon.paths import CONFIG


def base_config():
    return json.loads(CONFIG.read_text())


def test_valid_config_round_trips():
    config = base_config()
    assert validate_config(dict(config)) == config


@pytest.mark.parametrize("overrides", [
    {"party": []},
    {"game": "emerald"},
    {"max_decisions": 0},
    {"max_legs": 0},
    {"stuck_limit": 0},
    {"agent": "not-an-agent"},
])
def test_invalid_config_rejected(overrides):
    config = {**base_config(), **overrides}
    with pytest.raises(ValueError):
        validate_config(config)
