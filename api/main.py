"""FastAPI ingest endpoint — receives messages from the WhatsApp listener."""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent.pipeline import run_pipeline

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

app = FastAPI(title="WhatsApp Job Screener", version="0.1.0")


class Message(BaseModel):
    """Payload posted by listener.js for every WhatsApp group message."""

    group: str = Field(..., description="WhatsApp group display name")
    sender: str = Field(..., description="WhatsApp ID of the sender")
    text: str = Field(..., description="Raw message body")
    timestamp: int = Field(..., description="Unix epoch seconds")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(msg: Message) -> dict:
    result = await run_pipeline(msg.model_dump())
    return {"status": "ok", "result": result}
