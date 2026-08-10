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
- Follow-ups deepen the same event or task along different axes.
- Evaluation points measure the selected focus; unrelated KSA rows from the same
  competency unit are not added merely to increase grounding counts.
- Every task is paired with an interviewer instruction and distinct high,
  medium, and low observable-behavior anchors.
- Questions remain blind-hiring safe and do not infer protected attributes.

## Commands

One evidence cycle with a small official sample and template benchmark:

```powershell
python scripts\run_question_quality_loop.py --cycles 1
```

Fast local code gate without network or ALIO/MCP dependencies:

```powershell
python scripts\run_question_quality_loop.py --cycles 1 --skip-official --skip-alio
```

Daily loop (intended for a supervised runner):

```powershell
python scripts\run_question_quality_loop.py --cycles 0 --interval-minutes 1440
```

Model evaluation is opt-in because it can incur API cost:

```powershell
python scripts\run_question_quality_loop.py --cycles 1 --model-eval
```

Each cycle writes immutable logs and a summary under
`reports/question_quality_loop/`, then atomically updates `state.json`.

The AX evidence mapping, privacy boundary, operational APIs, and non-additive
promotion rules are documented in `docs/AX_EVIDENCE_GATE_MAP.md`.
