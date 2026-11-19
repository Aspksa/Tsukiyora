# Tsukiyora 10.0 — Unified Cognitive Life Cycle

Five user-approved development directions are integrated through the **existing**
core. This is a deterministic application workflow, not an independent agent,
autonomous LLM training, subjective emotions, or self-modifying software.

## One system, five source-backed views

| Direction | Existing owner(s) | What 10.0 adds |
|---|---|---|
| Dialogue | Living Dialogue 9, TopicRecall, V8 approved style | **One** bounded situational context composer for synchronous and SSE chat |
| Memory | MemoryStore, ConversationMemory, V5 review queues | Read-only state and review routing, no duplicate facts or "memory v10" database |
| Personality | PersonaContinuity, PersonaArc, SoulGrowth, Living Personality 8 | Approved tone hints only, clear progress, no modification to protected persona |
| Learning | SOUL ACADEMY, Skill Forge, Cognitive Supervisor 7, GrowthQuality | Existing evidence and real ratings shown side-by-side; no automatic promotion |
| Coordination | UnifiedCognitiveCore 5, Maintenance queue, V8 journal | One **additional local audit job**, one read-only panel, source-linked actions |

The old APIs remain valid, and original SQLite tables stay authoritative.

## Dialogue path

Both `/api/chat` and `/api/chat/stream` use
`core.life10.chat_context(core, message, conversation_id, history)`.

This is an **additional bounded part** of the user-triggered Cloud.ru prompt,
not a separate Cloud request. The context is assembled locally:

1. The fixed reviewed V9 natural-dialogue guidance.
2. A fixed tone suggestion from lexical task/learning/support/casual hints.
   These hints are fallible and **not** psychological claims.
3. Only explicitly approved V8 style hints still backed by user ratings.
4. Authentic, source-ID-linked older lines from the current conversation.
5. Authentic source-ID-and-conversation-ID-linked related lines from earlier
   conversations, omitting already included recent/old message IDs.

The composer adds only complete lines; it **never truncates a citation in the
middle**. The full composer is capped at 1,400 characters for ordinary chat
and 1,550 for streaming. Legacy guidance, retrieval permissions, refusal
rules, and the existing identity remain in charge. Old messages and document
excerpts are **untrusted data**, not fresh instructions or verified facts.

**Limitations:** lexical intent and retrieval are not semantic understanding;
an LLM cannot be guaranteed to ignore malicious quoted instructions, so
ordinary prompt-injection safeguards remain important. There is no claim of
live independent accuracy improvement. A user-triggered Cloud.ru request can
still be charged tokens for context even though V10 makes **zero additional
provider requests**. Legacy explicit paid experiments, if enabled by the
user, retain their previous permissions and budgets.

## Automatic safe local audit

`cog10_audit` is registered **once** in `Maintenance.JOBS` (every 3,600
seconds when it becomes due). It uses the pre-existing single SQLite queue,
single Python process worker, and chat-priority lock. After restart, the
same queue recovers the job; there is no extra worker or process.

The job reads counts of up to the 30 most recently rated answers, candidates
waiting for V7 proof, Academy curriculum flags, existing memory review tasks,
V8 proposals, active review goals and historical queue failures. A repeated
error signal is **three or more negative ratings among the last 30**; this
is only a reason for the user to inspect existing SOUL ACADEMY tasks, not a
claim that a skill was trained or improved.

At most **one** metadata-only V10 snapshot per UTC day is entered through
the established, size-bounded `soul8_journal`. The day is identified and
deduplicated transactionally. V8 autonomy level **0** suppresses this log.
No conversation text, passwords, API keys, or document bodies are written
to the snapshot. All actions are already owned by V5/V7/V8 jobs — 10.0
does not run them twice, create synthetic training pairs, edit memories,
approve skill candidates, edit the protected persona, or send Cloud.ru calls.

The journal is bounded to its existing 250 recent entries. An audit without
new source evidence is not evidence that LLM quality has increased.

## Unified local center

Settings → **Tsukiyora 10.0** reads:
`GET /api/soul/life10`.

It displays the five existing source systems, real feedback count, warning
signals, recent audit, and buttons to **open existing** Academy, V7, V8 or
V5 settings where the user can review and approve actions. The API is
read-only and exposes no raw messages, stored memory bodies, or source
document text. No parallel configuration panel for V8 permissions is
introduced.

## Safety and testing

No new dependencies, Docker, Ollama, agents, services or local model;
TypeScript + HTML5 Canvas + CSS stays in the web UI, Python remains the
single local API, and SQLite the single local database. Windows BAT,
file paths with spaces/Cyrillic, Linux and persisted data remain supported.

```sh
python -m unittest discover -s tests -v
python -m compileall -q launcher.py restore_memory.py tsukiyora tests scripts
python launcher.py --self-test
```

GitHub Actions verifies Python syntax and tests on Windows and Ubuntu,
frontend JS syntax, Windows BAT smoke mode and the million-record Atlas
stress check. None of these verifies live Cloud.ru quality/billing or
browser rendering on the user's own PC. The original
`soul/personality.json` is not modified.
