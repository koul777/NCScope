# Interview Question Quality Orchestration

## Objective

Continuously improve NCS-grounded interview questions without treating template
compliance as proof of human-readable quality. Every accepted generator change
must preserve official NCS traceability, blind-hiring safety, method structure,
natural Korean wording, and regression stability.

## Agent roles

| Role | Responsibility | Required evidence |
| --- | --- | --- |
| Source scout | Inspect the official NCS fair-hiring list, detail pages, and attachments; identify newly added or changed samples. | Fresh sample profile Markdown/CSV, source URL, collection timestamp |
| Generation engineer | Improve prompt, focus selection, templates, and post-processing without mixing NCS units or KSA rows. | Focused code diff and generated before/after examples |
| Quality auditor | Review actual questions for job relevance, observable behavior, natural wording, duplication, difficulty, and blind-hiring risk. | Per-question issues and regression fixtures |
| Release guardian | Run the complete gate and reject a promotion when any configured stage fails. | Test logs, benchmark logs, cycle summary, state JSON |

The roles work independently during investigation. Only the generation engineer
edits generator code. The quality auditor owns acceptance criteria, and the
release guardian decides promotion from recorded evidence rather than a claimed
score.

## Quality loop

1. Profile a bounded sample from the official NCS collections. Keep downloads
   cached and avoid aggressive polling of the public site.
   If the official listing is temporarily unreachable, the runner may profile
   previously downloaded archives using the newest matching report metadata.
   Such a report is labeled `cached` and proves parser regression stability,
   not that the public collection was freshly checked.
2. Run focused question-generation and official-sample regression tests.
3. Run the deterministic ALIO/JD benchmark so fallback quality is always
   measurable without model cost.
4. Validate reviewer-promoted golden/negative/regression cases and ensure
   rejected questions still enter the next-generation feedback context.
5. When explicitly enabled, run the model benchmark and keep model-origin,
   repaired, and template-fallback results separate.
6. Inspect failures and representative high-scoring questions manually. A score
   of 1.0 is not sufficient when wording is mechanical or the scenario is not
   operationally coherent.
7. Add the failure as a regression fixture before changing the generator.
8. Promote only when all configured stages pass and the before/after sample is
   better under the human rubric.

## Human rubric

- The main question evaluates one competency and asks for observable evidence.
- The scenario contains a real decision, constraint, risk, stakeholder, or
  output from the target work.
- Official `factorName` text remains traceable, but it is joined naturally:
  knowledge is used as evidence, skill is demonstrated, and attitude is shown
  through behavior. Avoid expressions equivalent to "apply an attitude."
- The evidence payload preserves the official factor verbatim, while the
  visible task removes only a trailing type noun such as `능력`, `기술`,
  `스킬`, or `지식`. Thus `소비자 패턴분석 능력` becomes the action object
  `소비자 패턴분석`, avoiding phrases such as `능력을 직접 수행` without
  losing the official source reference. The natural-wording gate rejects those
  suffix-plus-action combinations if a model response reintroduces them.
- Follow-ups deepen the same event or task along different axes.
- Experience questions accept a concrete work, project, or training-practice
  case so entry-level candidates can provide equivalent observable evidence;
  they still require the exact task, personal action, output, and result rather
  than asking whether the candidate has a named ability.
- Experience wording is staged as case selection, optional operational
  constraint, K/S/A-specific evidence, then result. The natural-wording gate
  rejects the former machine-like chain `발생하고 … 조건이고 … 조건인
  상황에서`. Discussion tasks state the KSA output once in the joint agreement,
  and job-knowledge tasks use separate knowledge/skill/attitude instructions
  instead of mechanically saying an attitude is a basis for a procedure.
- Evaluation points measure the selected focus; unrelated KSA rows from the same
  competency unit are not added merely to increase grounding counts.
- Every task is paired with an interviewer instruction and the five official
  sample labels (`탁월·우수·보통·미흡·부족`) with distinct observable-behavior
  anchors. Legacy high/medium/low fields remain in the API for compatibility.
  The anchors are also split by KSA type: knowledge uses application scope and
  exceptions, skill uses actual execution and output verification, and attitude
  uses choices under pressure and trade-offs.
- Questions remain blind-hiring safe and do not infer protected attributes.

## Runtime orchestration

The two interactive generation endpoints run the same deterministic sequence
after model generation or template fallback:

1. normalize the generated count, method, NCS unit, official KSA focus,
   follow-ups, evaluation points, task conditions, and behavior anchors;
2. reject a KSA-name restatement that merely asks whether related experience
   exists;
3. reject keyword-stuffed fragments, including fragments with a polite ending,
   unless they explicitly elicit a candidate response and contain enough task
   detail to state context, action/judgment, and outcome or constraint;
4. require method-specific observable evidence and KSA-type operationalization;
5. compare the candidate with the request-scoped prior-question history;
6. repair only the affected item with a new job constraint and stakeholder
   pressure, isolating exceptions per item;
7. attach official KSA evidence again and run the complete quality gate;
8. return stage counts and per-item reasons in
   `question_quality_orchestration`.

If every repair attempt is exhausted, the returned item is the original
candidate. Its original failure reasons plus `repair_exhausted` remain in
`final_issues`; issues from the last discarded candidate are exposed separately
as `last_candidate_issues`. This keeps operational diagnosis aligned with the
question the caller actually received.

When several official KSA factors are available, selection favors the evidence
shape of the method: experience favors skill; situation, discussion, and
in-basket favor attitude; and presentation, job-knowledge, and the current
verbal/data-analysis creative problem task favor knowledge. In-basket and
creative-problem formats deliberately do not prioritize arbitrary manual skills
that cannot be demonstrated in those exercises. This is only a priority order. If the
official unit exposes one type, the same method still converts that KSA into an
observable task rather than inventing a better-fitting factor.

NCS MCP KSA rows accept the documented snake-case fields and compatible
camel-case/name aliases. Type values such as `K/Knowledge`, `S/Skill`, and
`A/Attitude` are normalized to `지식/기술/태도` before balancing and question
generation, so a transport naming variation cannot silently erase the KSA
type. KSA collection round-robins competency elements before applying the
per-unit limit, and relevance ranking reserves available slots for distinct
K/S/A types before admitting a second factor of the same type. The default
ranked limit is three factors per unit.

The work-domain pack is selected from the competency detail/name before the
KSA factor. This prevents an overloaded factor token from changing the
construct—for example, `문서 보안 법규 지식` remains a document-management
task and cannot silently become a physical guard/patrol task merely because it
contains `보안`.

The browser keeps up to 500 unique questions for the current upload or manual
NCS context. Changing that context resets the history. The server receives the
whole bounded browser history for deterministic comparison. The server also
enforces its own newest-500-item and 1,600-character-per-item limits for API
clients, while only the most recent 20 questions are inserted into the model
prompt to keep prompt size stable.

Operational invariants:

- a second or later generation keeps the requested question count;
- the response does not silently reuse a recent exact or near-duplicate task;
- a repair exception is evidence on one item, not an unhandled request error;
- `needs_review` reports an unresolved item/count gap or an explicit
  operational warning such as fallback-adjustment degradation;
- every accepted repaired question passes both the KSA measurement audit and
  the full quality report.

Review persistence is serialized per quality run. SQLite takes an immediate
writer reservation before reading the active decision, and databases with row
locking use `SELECT ... FOR UPDATE`. This prevents concurrent reviews from
leaving two active decisions for one question. Review tokens, active decisions,
and negative feedback are verified again after a complete database reconnect.
Review tokens use a 192-bit `qqt_`-prefixed hex value. Machine-issued tokens are
verified by their persisted hash and are excluded from the API-key-like free-text
scanner; otherwise a random URL-safe token containing an `sk-` fragment can be
misclassified and make every otherwise valid review and rollback return 422.
The browser also keeps the review UI state by quality-run ID plus question hash.
Pagination or a card re-render therefore cannot re-enable an already submitted
decision or hide its rollback action. A new quality run clears that map.
All JSON responses explicitly declare `charset=utf-8`, which prevents legacy
Windows PowerShell clients from corrupting Korean question text and then
triggering a false question-hash mismatch when it is posted back for review.
An exact retry with the same run, question, reviewer, verdict, issue codes, and
note is idempotent: the active review ID is returned and no duplicate history
row is created. This makes a lost HTTP response safe to retry and prevents one
rollback from merely revealing an identical duplicate decision.
Rollback has the same protection: when the latest mutation is the same
reviewer/note rollback, the prior rollback event ID and restored decision are
returned without stepping back through another historical decision. A new
review after that event makes a later rollback a new mutation as expected.
Every new review also records the exact active decision it replaced. Therefore
`A → B → rollback-to-A → C → rollback` restores A, not B merely because B is
the next older database row. Older rows without this pointer retain a
compatibility fallback to chronological history.
Accepted review codes are also actionable generation rules: a
`missing_ksa_evidence` review injects the K/S/A measurement contract, while a
`method_task_mismatch` review injects the selected method's observable task
shape into the next prompt instead of a generic "수정하기" instruction.

The regression suite runs 30 consecutive generations across all seven methods
and all three KSA types (630 method/type/cycle cases), plus an API-level
generate-review-regenerate sequence. A separate long-run pressure check covers
230 generations per method/KSA case (4,830 cycles) across the former
200-question rollover boundary and against the current 500-question rolling
history; the deterministic constraint/stakeholder pool contains 1,152 unique
combinations before reuse.

Recorded overnight evidence includes a current-policy 630-cycle regeneration
matrix with zero empty, duplicate, unresolved, KSA-gate, or full-quality
failures. An earlier runtime-policy v5 pressure run covered 4,830 cycles across
the 500-question rollover boundary with the same zero-failure invariants. A
v6 rerun at that pressure size hit the 15-minute environment timeout and is not
counted as passing evidence. The lifecycle evidence is a
1,000-cycle review lifecycle run with 333 forced database reconnects, 2,000
intentionally invalid foreign-hash/tampered-text requests, 1,000 exact review
retries and 1,000 exact rollback retries that created no duplicate history rows,
and zero state failures after the token false-positive fix. A separate 500-run
concurrency simulation issues 7,000 review/rollback mutations from simultaneous workers and
retains exactly one active decision with zero failures. The reports are written to
`reports/question_quality_simulation/`; they are execution evidence for this
revision, not a substitute for periodic human sampling of real job families.

## KSA measurement contract

- **Knowledge**: use the factor as a decision basis, including scope,
  exceptions, and error risk.
- **Skill**: elicit execution order, concrete action, tools/evidence, output,
  and quality verification.
- **Attitude**: create pressure or a trade-off and elicit a visible choice or
  sustained behavior.

Forbidden pattern: `'{factorName} 능력'과 관련하여 실제 경험이 있으십니까?
말씀해 주세요.` The factor name is traceable evidence, not a complete
interview question.

Skill wording selects the observable verb from the factor object: a concrete
task is `수행`, a technique/method is `적용`, and a tool/system is `활용`.
For example, `수요예측 기법을 직접 수행` is rejected and rewritten as
`수요예측 기법을 직접 적용` while the official factor label remains attached
to evidence and the rating guide.

Source basis:

- NCS method definitions and examples:
  <https://www.ncs.go.kr/blind/bl02/RH-103-003-04.scdo>
- NCS official interview-question collection:
  <https://www.ncs.go.kr/blind/rh13/bbs_lib_list.do?libDstinCd=49&menuId=MN02020303>
- OPM structured interviews and common rating standards:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/structured-interviews/>
- OPM work samples and observable task outcomes:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/other-assessment-methods/work-samples-and-simulations/>
- OPM in-basket and group-discussion simulations:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/other-assessment-methods/assessment-centers/>
- OPM job-knowledge scope:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/other-assessment-methods/job-knowledge-tests/>

## Commands

One evidence cycle with a small official sample and template benchmark:

```powershell
python scripts\run_question_quality_loop.py --cycles 1
```

Fast local code gate without network or ALIO/MCP dependencies:

```powershell
python scripts\run_question_quality_loop.py --cycles 1 --skip-official --skip-alio
```

Deterministic recheck/regenerate simulation (7 methods × K/S/A × 30 cycles):

```powershell
python scripts\simulate_question_regeneration.py --cycles 30 --history-window 500
```

The simulator starts each method/KSA case with the explicitly forbidden
factor-name experience restatement, then reuses a candidate to exercise history
deduplication. It exits non-zero if any cycle is empty, repeated, unresolved,
or fails either the KSA measurement gate or full quality gate. JSON and Markdown
evidence are written under `reports/question_quality_simulation/`.

Generate-review-change-rollback-regenerate simulation with periodic DB
reconnection:

```powershell
python scripts\simulate_question_review_lifecycle.py --cycles 30 --reconnect-every 5
```

This simulator uses an isolated temporary SQLite database. Every cycle creates
a new quality run, records and changes one decision, rolls the latest decision
back, verifies exactly one active decision and the run status through the HTTP
API, then generates the next non-duplicate question. It also checks that an
invalid review token, a foreign question hash, and tampered question text are
rejected without mutating state, and that state remains consistent after the
database connection is disposed and recreated. Non-200 response details and the
full failing question are preserved only in failure evidence for diagnosis.
Review saves and rollbacks may carry `expected_review_id`. An exact lost-response
retry remains idempotent, while a delayed retry after another reviewer changed
the active decision returns HTTP 409 and cannot overwrite or roll back that
newer decision.
The browser locks only the stale question after a 409 and directs the reviewer
to regenerate from current state; it does not repeatedly resend the stale
mutation or block unrelated questions.

Concurrent review/rollback stress with periodic DB reconnection:

```powershell
python scripts\simulate_question_review_concurrency.py --rounds 50 --parallel-reviews 8 --mixed-operations 6 --reconnect-every 5
```

Each isolated run first receives simultaneous review decisions, then a mixed
wave of review replacements and rollbacks. It fails if any operation raises, if
review history is incomplete, if more or fewer than one decision remains active,
or if the run decision disagrees with the active review.

Live HTTP generation/review retry/rollback/regeneration smoke (requires the app
and its configured NCS MCP to be running):

```powershell
python scripts\simulate_question_review_live_http.py --base-url http://127.0.0.1:8015
```

This checks JSON UTF-8 transport, real NCS KSA retrieval, full generation
quality, exact review retry idempotency, changed-payload audit history,
rollback state, a second decision and rollback that must restore the true
predecessor rather than the neighboring database row, one-active-decision
consistency, and non-duplicate regeneration.
The live gate also verifies that delayed review and rollback retries are
rejected after an intervening decision, without adding an audit-history row.
It skips review calls when generation did not produce a valid run identity, so
a source-data failure is reported once instead of being obscured by a cascade
of invalid empty-ID requests. Report filenames include microseconds and a
random suffix so immediate retries cannot overwrite evidence from the prior
run. JSON and Markdown evidence also redact review tokens, API keys, and
authorization fields defensively.

The browser generation-context key includes the selected NCS codes/details,
notice and evaluation text, question plan, interview methods, and runtime
knobs. Changing one of these settings resets the rolling history and generation
offset; retrying the same configuration keeps both. A quality-passed NCS KSA
fallback is presented as an operational warning rather than a fatal generation
error, including when the final source is `quality_orchestrator_repair`.

`GET /health` bypasses the five-minute MCP tool-discovery cache, so a stopped
or reconfigured NCS MCP cannot remain falsely `reachable`. The cache itself is
scoped to the configured endpoint.

Daily loop (intended for a supervised runner):

```powershell
python scripts\run_question_quality_loop.py --cycles 0 --interval-minutes 1440
```

Model evaluation is opt-in because it can incur API cost:

```powershell
python scripts\run_question_quality_loop.py --cycles 1 --model-eval
```

Include the live operational gate in a finite evidence loop:

```powershell
python scripts\run_question_quality_loop.py --cycles 2 --live-http-base-url http://127.0.0.1:8015
```

Each cycle writes immutable logs and a summary under
`reports/question_quality_loop/`, then atomically updates `state.json`.
Cycle directories use a microsecond timestamp plus a random suffix, so rapid or
concurrent finite runs cannot claim the same evidence path. State updates use a
unique temporary file, an in-process lock, and bounded retry around the final
replace because Windows can briefly return `WinError 5` when concurrent writers
replace the same snapshot.
Finite runs execute back-to-back by default (`--cycles 2` waits only the
one-second safety minimum); unbounded `--cycles 0` runs retain a default daily
interval unless `--interval-minutes` is explicitly provided. This avoids a
surprising 24-hour pause between finite verification cycles.
The ALIO stage requires at least 50% of attempted documents to reach question
evaluation by default (`--alio-min-evaluated-doc-rate 0.5`). A configured but
unreachable NCS MCP, or a corpus that yields no evaluable documents, therefore
fails the cycle instead of producing a false green result.
It also requires a 100% ready rate across every adjusted question that was
actually generated (`--alio-min-template-ready-rate 1.0`). A single bad item
therefore fails promotion instead of disappearing inside a document average.
The default ALIO sample is eight recent cached documents: the former four-item
sample was too sensitive to two newly cached, non-evaluable multi-role files.
This larger default still exposes coverage failures in the report and does not
count blocked documents as successful question evaluations.

The AX evidence mapping, privacy boundary, operational APIs, and non-additive
promotion rules are documented in `docs/AX_EVIDENCE_GATE_MAP.md`.
