"""Loads the YAML under /config. Shared by llm, registry, and the planner."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.getenv("NEXUSGTM_CONFIG", Path(__file__).resolve().parent.parent / "config"))


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def llm_config() -> dict[str, Any]:
    return load("llm.yaml")


def departments_config() -> list[dict[str, Any]]:
    return load("departments.yaml").get("departments", [])


def playbook() -> dict[str, Any]:
    return load("playbook.yaml")
