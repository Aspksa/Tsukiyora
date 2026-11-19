# Cognitive Soul 6.0 — Autonomous Skill Forge

An offline, evidence-led procedural skill forge extending the single
Cognitive Soul 5.0 SQLite maintenance queue. All existing V1–V5 data,
APIs, user persona and history remain unchanged. No new runtime packages,
Docker, Ollama, services, or local LLMs.

## 15 capabilities

1. Automatic skill birth: after at least TWO distinct positive feedback
   events with substantive notes in one domain, an inactive skill candidate
   is produced. Ratings are signals, not verified proof of success.
2. Skill constructor: combines safe, allowlisted workflow steps; the
   automatic composite constructor combines previously shadow-tested
   procedures as drafts, never activates them.
3. Individual curriculum: domain-specific foundation/practice/review levels,
   based on feedback and local scenario coverage.
4. Learning from mistakes: after three real negative ratings on a used
   skill, a modified draft may be created. Existing versions are preserved.
5. Hierarchical skills: a new skill contains parent_id and a version number.
6. Missing-skill detection: repeated negative feedback with no active
   domain skill creates a dormant hypothesis.
7. Transfer: verified source recipes can become unverified drafts in
   another domain and must be checked independently.
8. Shadow testing: check four fixed workflow-order scenarios offline,
   without affecting live chats.
9. Automatic local testing: at most 6 candidates per background run on the
   same persistent scheduler. Never calls a model.
10. Skill competition: heuristic ordering by scenario coverage and
    separately recorded positive experiences; not proof of LLM quality.
11. Versioned evolution: SQLite tracks candidates, parents, tests, sources,
    outcomes and decisions.
12. Deduplication: a fingerprint prevents repeated identical procedures;
    revoked feedback pauses the corresponding skill. Archive, never delete
    the skill or its history.
13. Smart selection: only manually approved plans can influence chat.
    Instructions are built exclusively from vetted static text, never
    from user feedback, model output or executable scripts.
14. Offline background learning: cog6_discover, cog6_shadow, cog6_audit and
    cog6_curriculum share the ONE Cognitive Soul 5.0 SQLite queue.
15. Mastery memory: tracks source IDs, actual rated responses, structural
    trials, versions, and when the user disabled or archived a skill.

## Safety, evidence, and limitations

These are non-executable advisory workflows, NOT real code/tool skills,
unattended agents, automatic model fine-tuning, independent factual
verification, or proof of consciousness. Local shadow checks validate
the order and presence of permitted steps, not correctness of model answers.

Possible states: draft → shadow → approved (only with user confirmation),
then paused or archived. All 4 offline scenarios must pass before approval.
Eight real ratings with <=25% positive feedback automatically pause the
active skill; no replacement is auto-activated.

All source dialogue content stays in existing local SQLite. The newly
stored skill recipes contain only fixed step identifiers and source message
IDs. No original message content or API keys are copied into skills.

## Simple Cloud.ru ON/OFF switch and skill offers in chat

The new everyday control is **Cloud.ru самообучение: ВКЛ/ВЫКЛ**
in Cognitive Soul 6.0, mirrored by **Навыки · вкл/выкл** in the chat heading.

- OFF is the default. NO automatic paid requests occur while OFF.
- Switching ON requires an explicit confirmation warning that a
  Cloud.ru attempt may cost money and real token pricing is unknown.
  The toggle itself grants consent for **at most ONE potentially paid
  model call per UTC day** for Skill Forge, not unlimited spending.
- The existing provider's ResourceGuard quotas still apply. The Forge
  counter is reserved in SQLite before a call; disabling/re-enabling
  cannot reset the day's allowance. A given candidate is never
  retried automatically, even after a failure or restart.
- The Python worker uses only the existing V5 SQLite queue
  (cog6_cloud once per hour). It skips if no fully shadow-checked
  candidate exists, the main safe-local worker is paused, Cloud.ru
  is disconnected, or the chat is busy.
- The request sends only a static allowed step catalog + domain and
  the existing persona. No user conversations, memory text, private
  documents or explicit positive ratings go to the test request.
- The returned JSON is validated against fixed non-executable steps.
  Any valid new sequence is saved only as an inactive draft and
  later checked by the ordinary offline shadow worker.
- Disabling blocks new automatic requests but cannot retroactively
  cancel an already transmitted request.
- After the offline four-scenario structural check, **ready drafts
  are offered inside the chat** with "Применить навык" and "Отклонить".
  Only explicit approval activates one; dismissal archives the proposal
  but preserves the history. These are real SQLite cards, not invented
  chat messages. The chat checks for offers on startup, after responses,
  and periodically while open.
- To form candidates without requiring a typed positive feedback note:
  3 separate helpful ratings in a domain now suffice; 2 ratings with
  substantive explanatory notes still suffice.

The ON switch is a **call-count cap, NOT a money cap**. Review actual
Cloud.ru tariffs before opting in; the app cannot guarantee a ruble
amount from that switch. The previous advanced manual budget controls
are retained for backwards compatibility, but are no longer required
for the one-call/day automation switch. The old manually triggered
Cloud path still requires its own confirmations and quotas.

## Cloud.ru: separate budget approval, NEVER automatic by default

The default state remains OFF with zero authorized calls.
Switching ON later inside the app is an explicit one-call-per-day
permission. The old *manual* budget workflow below is still available.

1. Explicitly propose 1–20 potentially paid Cloud.ru attempts per UTC day.
   Proposal is pending and does not grant permission.
2. After a separate real budget agreement, explicitly approve the proposed
   count while acknowledging token prices are not known to the software.
   Revocation switches the budget off.
3. Explicitly confirm EACH individual model-assisted generation call.
   The counter reserves capacity BEFORE invoking Cloud.ru and counts even
   a failed attempt. The existing ResourceGuard limits also apply.

**Important:** a request-count limit does not guarantee a maximum
monetary charge without verified prices and billing. Obtain an actual
price and agree an acceptable budget before enabling this path.

When authorized, the cloud call sends only the *domain* and the static
allowlist of step identifiers as the task (plus the existing assistant
system persona). It does not send chat history, files, stored memory or
feedback. Cloud output must be JSON with exactly one steps array,
3–10 distinct allowlisted identifiers, starting with scope. Invalid
output is rejected; valid output becomes an inactive draft and is
not automatically tested on the real model or applied.

## API: localhost, same-origin JSON

Read endpoints:
- GET /api/soul/forge
- GET /api/soul/forge/skills
- GET /api/soul/forge/history
- GET /api/soul/forge/curriculum?domain=coding
- GET /api/soul/forge/compete?domain=coding
- GET /api/soul/forge/budget

Manual local actions:
- POST /api/soul/forge/propose with domain and confirmed:true
- POST /api/soul/forge/shadow with limit:8 and confirmed:true
- POST /api/soul/forge/variant with skill_id and confirmed:true
- POST /api/soul/forge/compose with left_id, right_id, confirmed:true
- POST /api/soul/forge/transfer with skill_id, target, confirmed:true
- POST /api/soul/forge/decide with skill_id, approve, confirmed:true
- POST /api/soul/forge/archive with skill_id, confirmed:true

Separate optional Cloud.ru authorization:
- POST /api/soul/forge/budget/request with calls, confirmed:true
- POST /api/soul/forge/budget/decide with approve:true,
  confirmed:true, ack_unpriced:true
- POST /api/soul/forge/cloud/draft with skill_id,
  confirm_cloud_calls:true

Cloud operations acquire the existing chat lock and never run in
background. Old 1–5 modules and their API paths remain in place.

## Interface and checks

Tsukiyora.bat → Settings → Experience and Biography → Cognitive Soul 6.0.
One black-and-gold panel presents skills, local tests, provenance, manual
approval, rollback, curricula, and the separate budget procedure.

    python -m unittest discover -s tests -v
    python launcher.py --self-test

GitHub Actions checks Windows, Linux, Windows BAT, and a million-record
SQLite stress test. Mock tests never pay for actual Cloud.ru requests.
Actual Cloud.ru operation on the user's PC requires separate approval.
