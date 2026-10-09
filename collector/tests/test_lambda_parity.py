"""The SAM Lambda (infra/sam/insights_lambda) ships vendored copies of the
collector's pure insights modules so it produces exactly what the collector's
scheduler produces. Fails if a copy is stale -- fix with:

    python infra/sam/sync_insights.py
"""

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "collector" / "app" / "insights"
_VENDORED = _REPO / "infra" / "sam" / "insights_lambda" / "reqly_insights"

MODULES = ("anomaly_detection.py", "deploys.py", "hints.py", "report.py")


@pytest.mark.parametrize("name", MODULES)
def test_vendored_module_is_identical(name):
    assert (_VENDORED / name).read_bytes() == (_SOURCE / name).read_bytes(), (
        f"infra/sam/insights_lambda/reqly_insights/{name} is stale; "
        "run: python infra/sam/sync_insights.py"
    )


@pytest.mark.parametrize("name", MODULES)
def test_vendored_modules_do_not_import_the_collector_app(name):
    # The Lambda package has no collector app to import from.
    source = (_SOURCE / name).read_text(encoding="utf-8")
    assert "from .." not in source and "from app" not in source
