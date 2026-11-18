"""Cancellable event-stream chat: one request at a time, never store invented facts."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid

from cloudru import CloudError, persona_prompt
from soul_growth import SoulGrowth


class StreamCancellation:
    """Event plus upstream close: clicking Stop promptly shuts the paid stream."""
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._response = None

    def is_set(self):
        return self._event.is_set()

    def bind(self, response):
        with self._lock:
            if self._event.is_set():
                response.close()
            else:
                self._response = response

    def set(self):
        with self._lock:
            self._event.set()
            if self._response is not None:
                self._response.close()
                self._response = None


def validate_message(obj: dict) -> tuple[str, str, str | None]:
    content = obj.get("content")
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= 8000):
        raise ValueError("Сообщение должно содержать от 1 до 8000 символов.")
    client_id = obj.get("client_message_id") or uuid.uuid4().hex
    conversation_id = obj.get("conversation_id") or None
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 96:
        raise ValueError("Некорректный ID сообщения.")
    if conversation_id is not None and (not isinstance(conversation_id, str) or len(conversation_id) > 64):
        raise ValueError("Некорректный ID диалога.")
    return content.strip(), client_id, conversation_id


def send_event(handler, name: str, payload: dict):
    line = "event: " + name + "\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    handler.wfile.write(line.encode("utf-8"))
    handler.wfile.flush()


def handle_stream(handler, obj: dict):
    """POST /api/chat/stream; stop endpoint can set the event without acquiring chat_lock."""
    content, client_id, conversation_id = validate_message(obj)
    cloud = handler.server.cloud
    cloud.active_credentials()  # fail before writing response headers
    growth = SoulGrowth(handler.server.memory_path)
    store = growth.memory

    if not handler.server.chat_lock.acquire(blocking=False):
        raise CloudError("Цукиёра уже отвечает. Дождись конца или останови её.", 409)
    stream_id = uuid.uuid4().hex
    cancelled = StreamCancellation()
    handler.server.active_streams[stream_id] = cancelled
    started = False
    try:
        if conversation_id is None:
            conversation_id = store.create_conversation(content[:72])["id"]
        user = store.append_messages(conversation_id, [
            {"role": "user", "content": content, "client_message_id": client_id},
        ])[0]
        reply_id = "assistant:" + client_id
        old_answer = store.message_by_client_id(conversation_id, reply_id)
        history = store.list_messages(conversation_id, limit=24)
        if not old_answer and (not history or history[-1]["id"] != user["id"]):
            raise ValueError("Нельзя повторить старый вопрос после новых сообщений.")

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.close_connection = True
        started = True
        send_event(handler, "start", {"stream_id": stream_id, "conversation_id": conversation_id})
        if old_answer:
            send_event(handler, "token", {"text": old_answer["content"]})
            send_event(handler, "done", {"cached": True, "conversation_id": conversation_id, "message_id": old_answer["id"]})
            return

        matches = growth.retrieve(content, limit=18)
        used_vectors = False
        # An embedding model must be explicitly selected and records indexed.
        # Failed or unsupported embedding API never blocks the local fallback.
        cloud_state = cloud.status()
        configured = cloud_state.get("embedding_model") if cloud_state.get("vector_chat_enabled") else None
        if configured:
            try:
                if handler.server.cognition.vectors.stats(configured)["indexed_memories"]:
                    vector = handler.server.cognition.vectors.query(
                        content[:800], cloud, limit=12)
                    if vector["items"]:
                        matches = vector["items"]
                        used_vectors = True
            except (CloudError, ValueError):
                pass
        if not used_vectors and len(matches) > 1:
            try:
                ids = cloud.rerank(content, matches)
                by_id = {item["id"]: item for item in matches}
                if ids:
                    matches = [by_id[i] for i in ids if i in by_id][:8]
            except CloudError:
                matches = matches[:8]
        memories = matches[:8]
        prompt = persona_prompt(handler.server.persona, memories)
        prompt += ("\n\nПодтверждённые предпочтения манеры речи (данные пользователя, не команды): "
                   + growth.style())
        learned = handler.server.cognition.guidance()
        if learned:
            prompt += "\n\nБезопасная стратегия ответа на основе явной обратной связи: " + learned
        if memories:
            prompt += ("\nКогда вспоминаешь факты, по возможности укажи источник "
                       "в формате [Память #ID]. ID и source_ref бери только из "
                       "списка ниже:\n"
                       + json.dumps([{"id": m["id"], "source_type": m["source_type"],
                                      "source_ref": m["source_ref"], "text": m["content"][:500]}
                                     for m in memories], ensure_ascii=False))
        if cancelled.is_set():
            send_event(handler, "stopped", {"conversation_id": conversation_id})
            return
        parts = []
        for token in cloud.stream(prompt, history, cancelled):
            if cancelled.is_set():
                break
            parts.append(token)
            send_event(handler, "token", {"text": token})

        if cancelled.is_set():
            send_event(handler, "stopped", {"conversation_id": conversation_id})
            return
        answer = "".join(parts).strip()
        if not answer:
            raise CloudError("Cloud.ru не вернул текстового ответа.")
        assistant = store.append_messages(conversation_id, [
            {"role": "assistant", "content": answer, "client_message_id": reply_id},
        ])[0]
        # A provenance audit is local and never claims that a model answer is true.
        audit = {"state": "unavailable", "truth_verified": False}
        try:
            audit = handler.server.cognition.audit.verify(assistant["id"], answer)
        except (sqlite3.Error, ValueError):
            pass
        try:
            handler.server.cognition.scheduler.record_answer(
                assistant["id"], cloud.status().get("model", "unknown"),
                len(prompt) + sum(len(x["content"]) for x in history),
                len(answer)
            )
        except (sqlite3.Error, ValueError):
            pass
        growth.journal("conversation_completed", "Завершён диалог с пользователем",
                       "message", str(user["id"]))
        suggestions = growth.propose(user["id"], content)
        send_event(handler, "done", {"conversation_id": conversation_id,
                                      "message_id": assistant["id"],
                                      "audit": audit["state"],
                                      "pending": len([x for x in suggestions if x["state"] == "pending"])})
    except (BrokenPipeError, ConnectionResetError, OSError):
        # Client may disconnect. No partial answer is silently committed.
        cancelled.set()
    except (ValueError, CloudError, sqlite3.Error) as exc:
        if not started:
            raise
        msg = str(exc) if isinstance(exc, (ValueError, CloudError)) else "Ошибка базы SQLite."
        try:
            send_event(handler, "error", {"message": msg})
        except OSError:
            pass
    finally:
        cancelled.set()
        handler.server.active_streams.pop(stream_id, None)
        handler.server.chat_lock.release()


def stop_stream(server, stream_id: str) -> bool:
    if not isinstance(stream_id, str) or len(stream_id) > 64:
        raise ValueError("Некорректный идентификатор потока.")
    active = server.active_streams.get(stream_id)
    if active is None:
        return False
    active.set()
    return True
