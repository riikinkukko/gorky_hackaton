"""Сквозные тесты каркаса: API работает на заглушках с первого дня."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.contracts import AssistantReply


def chat(client: TestClient, text: str, session: str = "s1") -> AssistantReply:
    r = client.post("/api/chat", json={"text": text, "session_id": session})
    assert r.status_code == 200, r.text
    return AssistantReply.model_validate(r.json())


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert {k: body[k] for k in ("db", "llm", "stt", "tts")} == dict.fromkeys(
        ("db", "llm", "stt", "tts"), "ok"
    )
    assert body["impl"]["dialog"] in ("fake", "real")


def test_list_today_uses_seed_data(client: TestClient) -> None:
    reply = chat(client, "Что у меня сегодня?")
    assert reply.action == "listed"
    assert [i.id for i in reply.items] == [688, 689]  # golden q11
    assert reply.debug and reply.debug["intent"] == "list_items"


def test_create_reminder_then_undo(client: TestClient) -> None:
    reply = chat(client, "Напомни мне завтра в 10 утра позвонить Ивану")
    assert reply.action == "created"
    item = reply.items[0]
    assert item.title == "Позвонить Ивану"
    assert item.due_at and item.due_at.isoformat() == "2026-10-22T10:00:00+03:00"
    assert "чт, 22 октября, 10:00" in reply.text

    undone = AssistantReply.model_validate(
        client.post("/api/undo", json={"session_id": "s1"}).json()
    )
    assert undone.action == "undone"
    items = client.get(
        "/api/items",
        params={"from": "2026-10-22T00:00:00+03:00", "to": "2026-10-23T00:00:00+03:00"},
    ).json()
    assert item.id not in [i["id"] for i in items]


def test_meeting_clarify_flow(client: TestClient) -> None:
    first = chat(client, "Поставь встречу с Иваном завтра")
    assert first.action == "clarify"
    assert first.text == "На какое время поставить встречу?"
    second = chat(client, "в три")
    assert second.action == "created"
    m = second.items[0]
    assert m.type == "meeting"
    assert m.starts_at and m.starts_at.isoformat() == "2026-10-22T15:00:00+03:00"


def test_search_past_meeting(client: TestClient) -> None:
    reply = chat(client, "Когда у меня была встреча с Иваном?")
    assert reply.action == "found"
    assert reply.items[0].id == 530  # golden q19


def test_sessions_and_users_are_isolated(client: TestClient) -> None:
    chat(client, "Поставь встречу с Иваном завтра", session="a")
    other = chat(client, "в три", session="b")
    assert other.action != "created"
    r = client.post(
        "/api/chat",
        json={"text": "Что у меня сегодня?", "session_id": "u2"},
        headers={"X-User-Id": "2"},
    )
    assert r.json()["items"] == []


def test_tts_and_ics(client: TestClient) -> None:
    r = client.post("/api/tts", json={"text": "Привет"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    ics = client.get("/calendar/demo.ics")
    assert ics.status_code == 200 and "BEGIN:VCALENDAR" in ics.text
    assert client.get("/calendar/nope.ics").status_code == 404


def test_ws_voice_end_to_end(client: TestClient) -> None:
    with client.websocket_connect("/ws/voice?session_id=ws1") as ws:
        ws.send_bytes(b"text:")
        ws.send_bytes("Что у меня сегодня?".encode())
        ws.send_json({"type": "end"})
        transcript = ws.receive_json()
        assert transcript == {"type": "transcript", "text": "Что у меня сегодня?", "final": True}
        reply = ws.receive_json()
        assert reply["type"] == "reply" and reply["action"] == "listed"
        assert [i["id"] for i in reply["items"]] == [688, 689]
        audio = ws.receive_bytes()
        assert audio[:4] == b"RIFF"

        # второе высказывание в том же соединении, default-фраза FakeSTT
        ws.send_bytes(b"\x1a\x45\xdf\xa3")  # «webm»
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "transcript"
        assert ws.receive_json()["type"] == "reply"
        ws.receive_bytes()


def test_clarify_answer_with_date_overrides_pending_date(client: TestClient) -> None:
    chat(client, "Поставь встречу с Иваном завтра", session="hd")
    reply = chat(client, "послезавтра в 11", session="hd")
    assert reply.action == "created"
    assert reply.items[0].starts_at.isoformat() == "2026-10-23T11:00:00+03:00"


def test_x_freeze_now_header(client: TestClient) -> None:
    r = client.post(
        "/api/chat",
        json={"text": "Что у меня сегодня?", "session_id": "fz"},
        headers={"X-Freeze-Now": "2026-10-24T10:00:00+03:00"},
    )
    assert [i["id"] for i in r.json()["items"]] == [691, 692, 693]  # golden q17, суббота


def test_undo_twice_is_noop(client: TestClient) -> None:
    chat(client, "Напомни мне завтра в 10 утра позвонить Ивану", session="u")
    assert chat(client, "отмени", session="u").action == "undone"
    assert chat(client, "отмени", session="u").action == "none"


def test_undo_button_resets_pending_clarify(client: TestClient) -> None:
    chat(client, "Напомни мне завтра в 10 утра позвонить Ивану", session="ub")
    chat(client, "Поставь встречу с Иваном завтра", session="ub")  # ждёт время
    r = client.post("/api/undo", json={"session_id": "ub"}).json()
    assert r["action"] in ("undone", "none")


def test_demo_users_can_create_in_postgres() -> None:
    """Регрессия со стенда: пользователи 2 и 3 демо-логина падали на FK items_user_id_fkey."""
    import os

    import pytest

    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("нужен PostgreSQL")
    from app.config import Settings
    from app.main import create_app
    from tests.conftest import NOW

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        nlu_impl="real",
        dialog_impl="real",
        storage_impl="postgres",
        database_url=url,
        freeze_now=NOW,
    )
    with TestClient(create_app(s)) as c:
        r = c.post(
            "/api/chat",
            json={"text": "Напомни мне завтра в 10 утра позвонить Ивану", "session_id": "fk3"},
            headers={"X-User-Id": "3"},
        )
        body = r.json()
        assert body["action"] == "created", body["text"]
        c.post("/api/undo", json={"session_id": "fk3"}, headers={"X-User-Id": "3"})
