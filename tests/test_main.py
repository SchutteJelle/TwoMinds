import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from main import (
    AgentConfig,
    ConverseRequest,
    HistoryEntry,
    SessionState,
    UserTurnRequest,
    app,
    build_messages,
    configure_logging,
    log_event,
    sse,
)


@pytest.fixture(autouse=True)
def clean_sessions():
    main.sessions.clear()
    yield
    main.sessions.clear()


@pytest.fixture
def http_client():
    return TestClient(app)


# ── configure_logging ──────────────────────────────────────────────────────────

class TestConfigureLogging:
    def test_returns_logger_named_twominds(self):
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)
        assert logger.name == "twominds"

    def test_default_log_level_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)

    def test_debug_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)

    def test_invalid_log_level_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NOTAREAL_LEVEL")
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)


# ── log_event ─────────────────────────────────────────────────────────────────

class TestLogEvent:
    def test_event_name_appears_in_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="twominds"):
            log_event("my_event")
        assert "my_event" in caplog.text

    def test_kwargs_appear_in_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="twominds"):
            log_event("my_event", key="hello", num=7)
        assert "hello" in caplog.text
        assert "7" in caplog.text

    def test_output_is_valid_json(self, caplog):
        with caplog.at_level(logging.INFO, logger="twominds"):
            log_event("structured_event", session_id="abc", status="ok")
        record = next(r for r in caplog.records if r.name == "twominds")
        payload = json.loads(record.message)
        assert payload["event"] == "structured_event"
        assert payload["session_id"] == "abc"
        assert payload["status"] == "ok"

    def test_non_serialisable_value_uses_str(self, caplog):
        class Opaque:
            def __repr__(self):
                return "Opaque()"

        with caplog.at_level(logging.INFO, logger="twominds"):
            log_event("opaque_event", obj=Opaque())
        assert "opaque_event" in caplog.text


# ── sse ───────────────────────────────────────────────────────────────────────

class TestSse:
    def test_starts_with_data_prefix(self):
        assert sse({"type": "test"}).startswith("data: ")

    def test_ends_with_literal_newline_sequence(self):
        # sse() uses \\n\\n (literal \n chars) not actual newlines
        assert sse({"type": "test"}).endswith("\\n\\n")

    def test_json_payload_is_parseable(self):
        data = {"type": "token", "speaker": "a1", "text": "hi"}
        result = sse(data)
        # Strip "data: " prefix (6 chars) and "\\n\\n" suffix (4 chars)
        json_part = result[6:-4]
        assert json.loads(json_part) == data

    def test_non_ascii_chars_are_escaped(self):
        result = sse({"text": "café"})
        # ensure_ascii=True means non-ASCII is encoded as \uXXXX
        assert "caf" in result
        assert "\\u" in result

    def test_nested_dict(self):
        data = {"outer": {"inner": 42}}
        result = sse(data)
        assert json.loads(result[6:-4]) == data


# ── AgentConfig ───────────────────────────────────────────────────────────────

class TestAgentConfig:
    def test_name_and_personality(self):
        agent = AgentConfig(name="Alice", personality="Curious and bold")
        assert agent.name == "Alice"
        assert agent.personality == "Curious and bold"

    def test_personality_defaults_to_empty_string(self):
        agent = AgentConfig(name="Bob")
        assert agent.personality == ""

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            AgentConfig()

    def test_name_cannot_be_omitted(self):
        with pytest.raises(ValidationError):
            AgentConfig(personality="Smart")


# ── ConverseRequest ───────────────────────────────────────────────────────────

class TestConverseRequest:
    def _minimal(self, **overrides):
        defaults = dict(
            agent1=AgentConfig(name="A"),
            agent2=AgentConfig(name="B"),
            topic="Test topic",
        )
        defaults.update(overrides)
        return ConverseRequest(**defaults)

    def test_valid_minimal_request(self):
        req = self._minimal()
        assert req.turns == 6

    def test_custom_turns(self):
        req = self._minimal(turns=4)
        assert req.turns == 4

    def test_turns_minimum_boundary(self):
        req = self._minimal(turns=2)
        assert req.turns == 2

    def test_turns_maximum_boundary(self):
        req = self._minimal(turns=12)
        assert req.turns == 12

    def test_turns_below_minimum_raises(self):
        with pytest.raises(ValidationError):
            self._minimal(turns=1)

    def test_turns_above_maximum_raises(self):
        with pytest.raises(ValidationError):
            self._minimal(turns=13)

    def test_topic_is_required(self):
        with pytest.raises(ValidationError):
            ConverseRequest(agent1=AgentConfig(name="A"), agent2=AgentConfig(name="B"))

    def test_agent1_is_required(self):
        with pytest.raises(ValidationError):
            ConverseRequest(agent2=AgentConfig(name="B"), topic="T")

    def test_agent2_is_required(self):
        with pytest.raises(ValidationError):
            ConverseRequest(agent1=AgentConfig(name="A"), topic="T")


# ── UserTurnRequest ───────────────────────────────────────────────────────────

class TestUserTurnRequest:
    def test_valid_text(self):
        req = UserTurnRequest(text="Hello there!")
        assert req.text == "Hello there!"

    def test_text_is_required(self):
        with pytest.raises(ValidationError):
            UserTurnRequest()

    def test_empty_string_passes_pydantic(self):
        req = UserTurnRequest(text="")
        assert req.text == ""


# ── SessionState ──────────────────────────────────────────────────────────────

class TestSessionState:
    def test_active_is_true_by_default(self):
        assert SessionState().active is True

    def test_user_queue_is_empty_by_default(self):
        assert SessionState().user_queue == []

    def test_queues_are_independent(self):
        s1, s2 = SessionState(), SessionState()
        s1.user_queue.append("msg")
        assert s2.user_queue == []

    def test_can_set_inactive(self):
        s = SessionState()
        s.active = False
        assert s.active is False

    def test_can_enqueue_messages(self):
        s = SessionState()
        s.user_queue.extend(["a", "b", "c"])
        assert len(s.user_queue) == 3


# ── HistoryEntry ──────────────────────────────────────────────────────────────

class TestHistoryEntry:
    def test_stores_speaker_and_text(self):
        entry = HistoryEntry(speaker="a1", text="Hello World")
        assert entry.speaker == "a1"
        assert entry.text == "Hello World"

    def test_human_speaker(self):
        entry = HistoryEntry(speaker="human", text="A question")
        assert entry.speaker == "human"


# ── build_messages ────────────────────────────────────────────────────────────

class TestBuildMessages:
    def test_a1_empty_history_yields_opening_prompt(self):
        msgs = build_messages("a1", [])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "begin" in msgs[0]["content"].lower()

    def test_a2_empty_history_yields_no_messages(self):
        assert build_messages("a2", []) == []

    def test_a1_own_turn_is_assistant(self):
        history = [HistoryEntry(speaker="a1", text="My thought")]
        msgs = build_messages("a1", history)
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "My thought"

    def test_a1_other_turn_is_user(self):
        history = [HistoryEntry(speaker="a2", text="Their reply")]
        msgs = build_messages("a1", history)
        assert msgs[1]["role"] == "user"

    def test_a2_own_turn_is_assistant(self):
        history = [HistoryEntry(speaker="a2", text="My reply")]
        msgs = build_messages("a2", history)
        assert msgs[0]["role"] == "assistant"

    def test_a2_other_turn_is_user(self):
        history = [HistoryEntry(speaker="a1", text="Opening")]
        msgs = build_messages("a2", history)
        assert msgs[0]["role"] == "user"

    def test_alternating_roles_from_a1_perspective(self):
        history = [
            HistoryEntry(speaker="a1", text="Turn 1"),
            HistoryEntry(speaker="a2", text="Turn 2"),
            HistoryEntry(speaker="a1", text="Turn 3"),
        ]
        msgs = build_messages("a1", history)
        # index 0 is opening prompt; 1,2,3 are history
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[3]["role"] == "assistant"

    def test_human_injected_turn_is_user_for_a1(self):
        history = [HistoryEntry(speaker="human", text="Interjection")]
        msgs = build_messages("a1", history)
        assert msgs[1]["role"] == "user"

    def test_human_injected_turn_is_user_for_a2(self):
        history = [HistoryEntry(speaker="human", text="Interjection")]
        msgs = build_messages("a2", history)
        assert msgs[0]["role"] == "user"

    def test_content_is_preserved(self):
        history = [HistoryEntry(speaker="a1", text="Preserved content")]
        msgs = build_messages("a1", history)
        assert any(m["content"] == "Preserved content" for m in msgs)


# ── /api/health endpoint ──────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self, http_client):
        assert http_client.get("/api/health").status_code == 200

    def test_ok_is_true(self, http_client):
        assert http_client.get("/api/health").json()["ok"] is True

    def test_configured_false_without_key(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", False):
            data = http_client.get("/api/health").json()
        assert data["configured"] is False

    def test_configured_true_with_key(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True):
            data = http_client.get("/api/health").json()
        assert data["configured"] is True


# ── /api/converse/{id}/user-turn endpoint ────────────────────────────────────

class TestQueueUserTurnEndpoint:
    def test_queues_message_successfully(self, http_client):
        sid = "session-1"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            res = http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "Hello"})
        assert res.status_code == 200
        assert res.json() == {"ok": True, "queued": 1}

    def test_message_is_stored_in_queue(self, http_client):
        sid = "session-2"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "Store me"})
        assert session.user_queue == ["Store me"]

    def test_empty_text_returns_400(self, http_client):
        sid = "session-3"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            res = http_client.post(f"/api/converse/{sid}/user-turn", json={"text": ""})
        assert res.status_code == 400

    def test_whitespace_only_text_returns_400(self, http_client):
        sid = "session-4"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            res = http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "   \t\n"})
        assert res.status_code == 400

    def test_nonexistent_session_returns_404(self, http_client):
        with patch.dict(main.sessions, {}, clear=True):
            res = http_client.post("/api/converse/ghost/user-turn", json={"text": "Hi"})
        assert res.status_code == 404

    def test_inactive_session_returns_404(self, http_client):
        sid = "dead-session"
        session = SessionState(active=False)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            res = http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "Hi"})
        assert res.status_code == 404

    def test_text_is_truncated_at_2000_chars(self, http_client):
        sid = "session-5"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "x" * 3000})
        assert len(session.user_queue[0]) == 2000

    def test_multiple_messages_increment_queued_count(self, http_client):
        sid = "session-6"
        session = SessionState(active=True)
        with patch.dict(main.sessions, {sid: session}, clear=True):
            http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "First"})
            res = http_client.post(f"/api/converse/{sid}/user-turn", json={"text": "Second"})
        assert res.json()["queued"] == 2


# ── /api/converse (SSE streaming) endpoint ───────────────────────────────────

def _make_mock_client(tokens=None):
    """Return a mock Anthropic client whose stream yields given tokens."""
    if tokens is None:
        tokens = ["Hello", " World"]

    def _make_stream(*args, **kwargs):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.text_stream = iter(tokens)
        return cm

    mock_client = MagicMock()
    mock_client.messages.stream.side_effect = _make_stream
    return mock_client


def _parse_sse(text: str) -> list[dict]:
    # sse() separates events with literal \\n\\n (not real newlines)
    events = []
    for chunk in text.split("\\n\\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            try:
                events.append(json.loads(chunk[6:]))
            except json.JSONDecodeError:
                pass
    return events


VALID_PAYLOAD = {
    "agent1": {"name": "Alice", "personality": "Analytical"},
    "agent2": {"name": "Bob"},
    "topic": "Artificial Intelligence",
    "turns": 2,
}


class TestConverseEndpoint:
    def test_returns_503_without_api_key(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", False), \
             patch.object(main, "client", None):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)
        assert res.status_code == 503

    def test_returns_400_for_invalid_payload(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", MagicMock()):
            res = http_client.post("/api/converse", json={"garbage": True})
        assert res.status_code == 400

    def test_returns_400_for_blank_topic(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            res = http_client.post("/api/converse", json={**VALID_PAYLOAD, "topic": "   "})
        assert res.status_code == 400

    def test_returns_400_for_blank_agent_name(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            payload = {**VALID_PAYLOAD, "agent1": {"name": ""}}
            res = http_client.post("/api/converse", json=payload)
        assert res.status_code == 400

    def test_start_event_contains_session_id_and_names(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        events = _parse_sse(res.text)
        start = next((e for e in events if e.get("type") == "start"), None)
        assert start is not None
        assert start["agent1Name"] == "Alice"
        assert start["agent2Name"] == "Bob"
        assert start["topic"] == "Artificial Intelligence"
        assert "sessionId" in start

    def test_done_event_is_emitted(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        events = _parse_sse(res.text)
        assert any(e.get("type") == "done" for e in events)

    def test_token_events_are_emitted(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client(["Hi", " there"])):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        token_events = [e for e in _parse_sse(res.text) if e.get("type") == "token"]
        assert len(token_events) > 0

    def test_turn_start_events_alternate_speakers(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        turn_starts = [e for e in _parse_sse(res.text) if e.get("type") == "turn_start"]
        assert len(turn_starts) == 2
        assert turn_starts[0]["speaker"] == "a1"
        assert turn_starts[0]["name"] == "Alice"
        assert turn_starts[1]["speaker"] == "a2"
        assert turn_starts[1]["name"] == "Bob"

    def test_turn_end_events_are_emitted(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client(["text"])):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        turn_ends = [e for e in _parse_sse(res.text) if e.get("type") == "turn_end"]
        assert len(turn_ends) == 2

    def test_session_is_removed_after_conversation(self, http_client):
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            http_client.post("/api/converse", json=VALID_PAYLOAD)

        assert len(main.sessions) == 0

    def test_custom_personality_accepted(self, http_client):
        payload = {
            "agent1": {"name": "Philosopher", "personality": "Speaks in riddles"},
            "agent2": {"name": "Scientist"},
            "topic": "Consciousness",
            "turns": 2,
        }
        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", _make_mock_client()):
            res = http_client.post("/api/converse", json=payload)

        events = _parse_sse(res.text)
        assert any(e.get("type") == "start" for e in events)

    def test_error_event_emitted_when_stream_raises(self, http_client):
        def _bad_stream(*args, **kwargs):
            cm = MagicMock()
            cm.__enter__ = MagicMock(side_effect=RuntimeError("API down"))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = _bad_stream

        with patch.object(main, "HAS_ANTHROPIC_API_KEY", True), \
             patch.object(main, "client", mock_client):
            res = http_client.post("/api/converse", json=VALID_PAYLOAD)

        events = _parse_sse(res.text)
        assert any(e.get("type") == "error" for e in events)
