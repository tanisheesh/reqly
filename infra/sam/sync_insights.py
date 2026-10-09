"""Copies the collector's pure insights modules into the Lambda package.

The weekly-insights Lambda must produce exactly what the collector's own
scheduler produces, without depending on the collector package (which pulls
in FastAPI, APScheduler, ...). So it ships byte-identical copies of the
modules that hold the logic. Run this after changing any of them:

    python infra/sam/sync_insights.py

collector/tests/test_lambda_parity.py fails CI if the copies are stale.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "collector" / "app" / "insights"
TARGET = REPO / "infra" / "sam" / "insights_lambda" / "reqly_insights"

# Modules with no imports from the collector app (stdlib/asyncpg only).
VENDORED_MODULES = ("anomaly_detection.py", "deploys.py", "hints.py", "report.py")

INIT = '''"""Vendored copies of collector/app/insights modules -- DO NOT EDIT.

Regenerate with: python infra/sam/sync_insights.py
"""
'''


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "__init__.py").write_text(INIT, encoding="utf-8", newline="\n")
    for name in VENDORED_MODULES:
        shutil.copyfile(SOURCE / name, TARGET / name)
        print(f"copied {name}")


if __name__ == "__main__":
    main()
