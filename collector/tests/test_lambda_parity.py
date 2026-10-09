"""The SAM Lambda (infra/sam/insights_lambda/handler.py) is deliberately
self-contained, so it carries a copy of the anomaly detector and the LLM
prompt. These checks fail CI if the copies drift from the collector's."""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HANDLER = (_REPO / "infra/sam/insights_lambda/handler.py").read_text(encoding="utf-8")


def test_lambda_anomaly_detection_matches_collector():
    collector = (_REPO / "collector/app/insights/anomaly_detection.py").read_text(encoding="utf-8")
    body = collector[collector.index("Z_THRESHOLD = "):].strip()
    assert body in _HANDLER


def test_lambda_system_prompt_matches_collector():
    collector = (_REPO / "collector/app/insights/groq_client.py").read_text(encoding="utf-8")
    prompt = re.search(r'SYSTEM_PROMPT = """(.*?)"""', collector, re.S).group(1)
    assert f'_SYSTEM_PROMPT = """{prompt}"""' in _HANDLER
