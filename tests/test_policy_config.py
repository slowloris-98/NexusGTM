"""Policy lives in playbook.yaml, not in the revops prompts.

These guard against silent drift: the failure mode is an edit to playbook.yaml
that moves the planner's guidance while the agents keep their old hardcoded
numbers, so every test here changes the config and asserts the prompt followed.
"""

from __future__ import annotations

import pytest
import yaml

from core import config
from departments.revops import server


@pytest.fixture
def policy(tmp_path, monkeypatch):
    """Write a playbook to a temp config dir and point the loader at it."""

    def write(**overrides):
        pb = {
            "icp": "Widget makers, 10-20 employees, Antarctica.\n",
            "thresholds": {"route_to_sales": 90, "nurture": 65, "disqualify": 30},
            "negative_signals": ["uses_fax", "pays_in_cash"],
        }
        pb.update(overrides)
        (tmp_path / "playbook.yaml").write_text(yaml.safe_dump(pb), encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.load.cache_clear()
        return pb

    yield write
    config.load.cache_clear()  # do not leak the temp playbook into later tests


def test_score_bands_come_from_the_playbook(policy):
    policy()
    bands = server.render_score_bands()

    assert "90+ to ae_direct" in bands
    assert "65-89 to sdr_nurture" in bands
    assert "below 30 to disqualified" in bands
    # The shipped defaults must not survive a config change.
    assert "80" not in bands and "60" not in bands and "40" not in bands


def test_every_negative_signal_reaches_the_prompt(policy):
    pb = policy()
    rendered = server.render_negative_signals()

    for signal in pb["negative_signals"]:
        assert signal in rendered


def test_icp_comes_from_the_playbook(policy):
    policy()

    # Folded YAML keeps a trailing newline; the prompt concatenates, so it must go.
    assert server.render_icp() == "Widget makers, 10-20 employees, Antarctica."


def test_missing_policy_raises_rather_than_falling_back(policy):
    policy(thresholds={"route_to_sales": 90})

    with pytest.raises(KeyError):
        server.render_score_bands()


def test_shipped_playbook_satisfies_the_agents():
    """The real config/playbook.yaml carries every key the prompts require."""
    config.load.cache_clear()

    assert server.render_icp()
    assert server.render_negative_signals()
    assert server.render_score_bands()
