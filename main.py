import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def configure_logging() -> logging.Logger:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("twominds")


logger = configure_logging()


def log_event(event: str, **kwargs: object) -> None:
    payload = {"event": event, **kwargs}
    logger.info(json.dumps(payload, ensure_ascii=True, default=str))


ENV_PATH = BASE_DIR / ".env"
HAS_DOTENV_FILE = ENV_PATH.exists()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
HAS_ANTHROPIC_API_KEY = bool(ANTHROPIC_API_KEY)
PORT = int(os.getenv("PORT", "3000"))
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")

client = Anthropic(api_key=ANTHROPIC_API_KEY) if HAS_ANTHROPIC_API_KEY else None


class AgentConfig(BaseModel):
    name: str
    personality: str = ""


class ConverseRequest(BaseModel):
    agent1: AgentConfig
    agent2: AgentConfig
    topic: str
    turns: int = Field(default=6, ge=2, le=12)


class UserTurnRequest(BaseModel):
    text: str


@dataclass
class SessionState:
    active: bool = True
    user_queue: list[str] = field(default_factory=list)


@dataclass
class HistoryEntry:
    speaker: str
    text: str


sessions: dict[str, SessionState] = {}
sessions_lock = asyncio.Lock()

app = FastAPI()


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"ok": True, "configured": HAS_ANTHROPIC_API_KEY}


@app.post("/api/converse/{session_id}/user-turn")
async def queue_user_turn(session_id: str, payload: UserTurnRequest) -> dict[str, object]:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    async with sessions_lock:
        session = sessions.get(session_id)
        if not session or not session.active:
            raise HTTPException(
                status_code=404,
                detail="Conversation session not found or no longer active.",
            )
        session.user_queue.append(text[:2000])
        queued = len(session.user_queue)

    log_event("user_turn_queued", session_id=session_id, queued=queued)
    return {"ok": True, "queued": queued}


def build_messages(speaker_key: str, history: list[HistoryEntry]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    if speaker_key == "a1":
        messages.append(
            {
                "role": "user",
                "content": "Please begin the discussion with your opening thoughts.",
            }
        )
        for entry in history:
            messages.append(
                {
                    "role": "assistant" if entry.speaker == "a1" else "user",
                    "content": entry.text,
                }
            )
    else:
        for entry in history:
            messages.append(
                {
                    "role": "assistant" if entry.speaker == "a2" else "user",
                    "content": entry.text,
                }
            )

    return messages


def sse(data: dict[str, object]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=True)}\\n\\n"


@app.post("/api/converse")
async def converse(request: Request) -> StreamingResponse:
    if not HAS_ANTHROPIC_API_KEY or client is None:
        raise HTTPException(
            status_code=503,
            detail="Missing ANTHROPIC_API_KEY. Set the environment variable and restart the server.",
        )

    try:
        raw_payload = await request.json()
        payload = ConverseRequest.model_validate(raw_payload)
    except Exception as exc:
        log_event("converse_validation_error", error=str(exc), exc_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="agent1, agent2, and topic are required")

    if not payload.topic.strip() or not payload.agent1.name.strip() or not payload.agent2.name.strip():
        raise HTTPException(status_code=400, detail="agent1, agent2, and topic are required")

    session_id = str(uuid4())
    session = SessionState(active=True)
    history: list[HistoryEntry] = []

    async with sessions_lock:
        sessions[session_id] = session

    log_event(
        "conversation_started",
        session_id=session_id,
        agent1=payload.agent1.name,
        agent2=payload.agent2.name,
        turns=payload.turns,
    )

    async def event_stream() -> AsyncIterator[str]:
        yield sse(
            {
                "type": "start",
                "sessionId": session_id,
                "agent1Name": payload.agent1.name,
                "agent2Name": payload.agent2.name,
                "topic": payload.topic,
            }
        )

        try:
            for i in range(payload.turns):
                if await request.is_disconnected():
                    log_event("client_disconnected", session_id=session_id)
                    break

                async with sessions_lock:
                    queued_messages = list(session.user_queue)
                    session.user_queue.clear()

                for user_text in queued_messages:
                    history.append(HistoryEntry(speaker="human", text=user_text))
                    yield sse({"type": "user_injected", "name": "You", "text": user_text})

                is_a1 = i % 2 == 0
                current = payload.agent1 if is_a1 else payload.agent2
                other = payload.agent2 if is_a1 else payload.agent1
                speaker_key = "a1" if is_a1 else "a2"

                personality = current.personality.strip() or "You are thoughtful, concise, and respectful."
                system = (
                    f"You are {current.name}. {personality}\\n\\n"
                    f"You are engaged in an intellectual dialogue with {other.name} on this topic: \"{payload.topic}\"\\n\\n"
                    "Guidelines:\\n"
                    "- Respond directly to what was just said\\n"
                    "- Be conversational and genuine - stay fully in character\\n"
                    "- Keep your response to 2-3 focused paragraphs of flowing prose\\n"
                    "- No bullet points, numbered lists, or headers"
                )

                yield sse({"type": "turn_start", "speaker": speaker_key, "name": current.name})

                text = ""
                log_event(
                    "turn_started",
                    session_id=session_id,
                    turn=i + 1,
                    speaker=speaker_key,
                    speaker_name=current.name,
                )

                with client.messages.stream(
                    model=MODEL,
                    max_tokens=1024,
                    system=system,
                    messages=build_messages(speaker_key, history),
                ) as stream:
                    for token in stream.text_stream:
                        if await request.is_disconnected():
                            log_event("client_disconnected", session_id=session_id)
                            return
                        text += token
                        yield sse({"type": "token", "speaker": speaker_key, "text": token})

                if text:
                    history.append(HistoryEntry(speaker=speaker_key, text=text))
                    yield sse({"type": "turn_end", "speaker": speaker_key})
                    log_event(
                        "turn_completed",
                        session_id=session_id,
                        turn=i + 1,
                        speaker=speaker_key,
                        chars=len(text),
                    )

            yield sse({"type": "done"})
            log_event("conversation_done", session_id=session_id, history_items=len(history))
        except Exception as exc:  # noqa: BLE001
            log_event("conversation_error", session_id=session_id, error=str(exc))
            yield sse({"type": "error", "message": str(exc)})
        finally:
            async with sessions_lock:
                session.active = False
                sessions.pop(session_id, None)
            log_event("session_closed", session_id=session_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.on_event("startup")
async def on_startup() -> None:
    log_event("startup", dotenv_found=HAS_DOTENV_FILE, env_path=str(ENV_PATH))
    log_event("anthropic_key", configured=HAS_ANTHROPIC_API_KEY)
    log_event("server_ready", url=f"http://localhost:{PORT}")


app.mount("/", StaticFiles(directory=BASE_DIR / "public", html=True), name="public")
