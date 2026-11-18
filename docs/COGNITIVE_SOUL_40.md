# Tsukiyora Cognitive Soul 4.0 — controlled evolutionary learning

12 capabilities implemented in a local Python/SQLite extension. These are
**auditable experiments** and do not constitute consciousness, general
intelligence growth, automatic self-modifying software, or LLM fine-tuning.

## Capabilities

1. **Strategy generator**: makes 3–5 new candidates by combining the fixed,
   vetted techniques evidence, alternatives, uncertainty, structure,
   verification, brevity and edge_cases; no arbitrary model-generated code,
   URLs or untrusted memories become instructions.
2. **Evolutionary selection**: stores generation, parent and recipe; chooses
   a parent by accumulated successful independently tested outcomes. Each
   generation tries new combinations; none are activated automatically.
3. **Error learning**: repeated negative user ratings cause the local
   maintenance/feedback cycle to propose dormant candidates. Ratings show
   dissatisfaction, not objectively proven root causes.
4. **Meta-selection**: after manual promotion, route each new chat answer
   to the active strategy for its predicted domain. This is a simple
   keyword heuristic, not full semantic understanding; the user can inspect
   and revoke active strategies.
5. **Curriculum**: domain-specific progression foundation/practice/challenge,
   based on test coverage and exact-match accuracy; uses user-entered train
   questions only. Does not automatically pay for experiments.
6. **Specialization**: independent candidate pools for logic, coding,
   legal, RAG and documents.
7. **Transfer**: user may explicitly evaluate a candidate on holdout tasks
   from another domain. The system records those cross-domain scores but
   cannot automatically promote a strategy in its original domain based
   solely on transfer.
8. **Multi-layer review**: distinguishes known-safe prompt recipes, objective
   exact-match outcomes, measured regressions, latency, call counts,
   unknown factual truth and untested cross-domain claims.
9. **Scenario simulation**: same-model paired baseline/candidate model
   trials on independently authored holdout questions. Gold answers stay in
   local SQLite; no personal memory or source documents travel with trials.
10. **Evolution memory**: persistent SQLite generations, genealogy,
    candidate state, per-case results and events. No raw model answers are
    retained in test telemetry.
11. **Regression guard**: after a manually activated strategy receives
    at least 8 real user ratings, <=25% positive ratings pauses it
    automatically; no automatic adoption of a substitute.
12. **Cognitive budget**: 1–6 pairs of answers per explicitly confirmed
    test (2–12 potentially paid Cloud.ru calls), max 24 locally reserved
    experiment calls per UTC day, additional Cloud.ru provider quotas,
    and a read-only prioritization plan.

## Evidence thresholds

The old 4-question Cognitive Soul 3.0 benchmark is left intact. V4 uses a
different, **stricter and opt-in** path:

- 30 **distinct** holdout questions for a candidate's original domain, all
  evaluated on the same Cloud.ru model. A run contains at most 6 pairs.
- Candidate gain >=10 percentage points in exact-match accuracy, and
  regressions limited to at most max(1, floor(10% of N)) of evaluated items.
- A candidate may only be activated after user confirmation. Normalized
  exact matching on short labels does **not prove** factual or general
  cognitive improvement; it is a limited evaluation protocol.
- The user authors reference answers. No independent verification of
  their truth. Reusing the holdout across many candidates risks
  overfitting; replace the test set with unseen, domain-representative tasks
  for strong claims. Do not insert personal data into test questions.
- Different models are grouped separately. A model change during a run
  invalidates that run. Failed/incomplete runs never qualify for promotion.

## Data, privileges, and cost safety

- All new state is stored in existing SQLite and covered by existing
  storage/recovery logic. No new service or dependency, no Docker/Ollama.
- The engine never initiates Cloud.ru requests from background maintenance:
  the user must confirm each batch. If quotas do not allow the needed
  number of calls, the batch cannot start.
- The budget counts *reserved attempts*, not actual token billing.
  You may need to increase the normal Cloud.ru per-minute request limit
  to 12 to run a full batch, or choose a smaller batch.
- The evaluation sends only the current **question** and the protected
  assistant persona + strategy. Gold answers remain local and
  are not included in the request. Some test questions themselves may
  contain sensitive text: user is responsible for removing this.
- Protected persona, approved memory and source files are never modified.
  The system changes only optional instructions after manual approval.
- A crashed running evaluation blocks new evaluations until a user has
  checked it is no longer active and explicitly marks it failed.

## HTTP API (same-origin local only)

GET /api/soul/evolution4 — candidate states, results, active strategies
GET /api/soul/evolution4/cases — local train/holdout data including gold
GET /api/soul/evolution4/signals — negative feedback and provenance audit
GET /api/soul/evolution4/budget — read-only budget planning
GET /api/soul/evolution4/curriculum?domain=coding — domain training plan

POST /api/soul/evolution4/generate
{"domain":"coding","count":4,"confirmed":true}

POST /api/soul/evolution4/case
{"domain":"coding","split":"holdout",
 "question":"What is 2 + 2?","expected":"4","confirmed":true}

POST /api/soul/evolution4/evaluate
{"candidate_id":1,"evaluation_domain":"coding",
 "batch_size":6,"confirm_cloud_calls":true}

POST /api/soul/evolution4/peer-review
{"candidate_id":1}

POST /api/soul/evolution4/transfer
{"candidate_id":1}

POST /api/soul/evolution4/decision
{"candidate_id":1,"approve":true,"confirmed":true}

Explicit rollback: the same /decision with approve:false, confirmed:true.

POST /api/soul/evolution4/recover
{"run_id":1,"confirmed":true}

All endpoints use the existing Python HTTP server. Same-origin checks are
mandatory for writes. Results are not a substitute for manual review.

## Manual check

Start Tsukiyora.bat; open Settings → Experience and Biography →
Cognitive Soul 4.0. Generate four local candidate recipes, add >=30
independent domain holdout questions with known short answers, select
an available Cloud.ru model, and run a sequence of paid pairwise batches.
Compare exact-match outcomes and regressions. The first eligible candidate
can only be activated after user confirmation. Test rollback and negative
feedback monitoring. Real Cloud.ru tests cannot be performed in CI without
a private user key; CI runs mocked provider tests only.

CI:
    python -m unittest discover -s tests -v
    python launcher.py --self-test
