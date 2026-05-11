"""Pipeline entry point — used by the FastAPI ingest endpoint.

Orchestration logic lives in agent/graph.py as a LangGraph StateGraph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from agent.graph import CONFIDENCE_THRESHOLD, _pipeline_graph

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    action: str                        # "stored" | "skipped" | "partial"
    reason: Optional[str] = None      # set when action == "skipped"
    job: Optional[dict] = None        # single job (legacy, kept for tests)
    job_id: Optional[int] = None      # single job id (legacy, kept for tests)
    stored: Optional[list] = None     # list of stored job dicts (multi-job)
    skipped: Optional[list] = None    # list of {job, reason} dicts (multi-job)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    message: dict,
    *,
    notify: bool = True,
) -> dict[str, Any]:
    """Run a single message through the full screening pipeline.

    Args:
        message: dict with keys ``text``, ``group``, ``sender``, ``timestamp``.

    Returns:
        Dict describing what happened — see :class:`PipelineResult`.
    """
    text: str = message.get("text", "")
    if not text.strip():
        return PipelineResult("skipped", reason="empty message").to_dict()

    final_state = await _pipeline_graph.ainvoke({
        "message": message,
        "notify": notify,
    })
    return final_state["result"]


# ---------------------------------------------------------------------------
# Manual smoke test:  python -m agent.pipeline
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import json
    import os

    from dotenv import load_dotenv
    load_dotenv()

    sample = {
        "group": "Tech Jobs TLV",
        "sender": "demo",
        "text": (
            "Hiring a Backend Engineer at Acme — Python/FastAPI, Tel Aviv (hybrid). "
            "Stock options + competitive salary. DM @recruiter or email jobs@acme.io"
        ),
        "timestamp": 1700000000,
    }
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    else:
        from db.init_db import init_db
        init_db()

        print("Running smoke test — sending a sample message through the pipeline...")
        try:
            result = asyncio.run(run_pipeline(sample, notify=False))
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as error:
            print(f"Error: {error}")
