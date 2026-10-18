"""Repo metadata that GitHub/HACS parse but no runtime code touches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize(
    "path", sorted((ROOT / ".github").rglob("*.yml")), ids=lambda p: p.name
)
def test_github_yaml_parses(path: Path) -> None:
    assert yaml.safe_load(path.read_text()) is not None


def test_versions_and_translations() -> None:
    integration = ROOT / "custom_components/eco_plugs_local"
    manifest = json.loads((integration / "manifest.json").read_text())
    assert manifest["version"]
    json.loads((integration / "translations/en.json").read_text())
    json.loads((ROOT / "hacs.json").read_text())
