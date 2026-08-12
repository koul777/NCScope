# Structured Interview Guide

## 1. Purpose

This guide fixes the interview-method contract used by NCScope when generating
NCS-grounded structured interview questions. Each question must measure one job
competency, stay blind-hiring safe, and preserve NCS code, competency unit, KSA
evidence, follow-up questions, and evaluation points.

## 2. Core Principles

- Ask one main question per competency and make the answer observable.
- Use job-description duties, NCS competency units, performance criteria, and
  KSA factors as the evidence base.
- Avoid school, region, family, age, gender, health, religion, appearance,
  marital status, military status, or other blind-hiring cues.
- Do not ask generic personality questions unless the job description or NCS
  evidence makes the behavior measurable.
- Follow-ups must deepen the same main question rather than become unrelated
  standalone questions.
- Evaluation points must describe assessable evidence, not vague attitudes.

### 2.1 KSA must become an observable task

Repeating an official KSA `factorName` is necessary for traceability, but it is
not sufficient evidence that the question measures that KSA. Never ask only
whether the applicant has experience "related to" a named ability. For
example, a question equivalent to "Do you have actual experience related to
document verification ability? Please tell us" must be rejected.

- Knowledge: require the applicant to use the knowledge as a decision basis,
  explain its scope or exception, and identify the risk of incorrect use.
- Skill: require an execution sequence, concrete actions, tools or evidence,
  an output, and a way to verify output quality.
- Attitude: place the applicant under a realistic pressure or trade-off and
  require a visible choice or consistent behavior. Do not ask whether the
  applicant has "applied an attitude."
- Experience interviews may elicit past evidence, but every other method must
  observe the KSA through that method's task rather than ask about experience.

## 3. 질문 유형별 작성 기법

### 3.1 경험면접

- Main question: ask for a past job-relevant or similar experience.
- Required shape: situation, applicant's own role, concrete action, result or
  learning.
- Follow-ups: clarify context, personal contribution, difficulty handling,
  result evidence, and improvement.
- Evaluation focus: specific situation, role and action, logic of approach,
  performance evidence, learning.

### 3.2 상황면접

- Main question: present a realistic job scenario with a decision point.
- Required shape: situation, judgment criteria, action order, risk or
  stakeholder response.
- Follow-ups: facts to verify first, basis for action, communication sequence,
  fallback if the result differs, prevention of recurrence.
- Evaluation focus: judgment criteria, risk recognition, action priority,
  stakeholder handling.

### 3.3 발표면접

- Main question: give an analysis or improvement task to present.
- Required shape: data or current-state diagnosis, root-cause analysis, at
  least two alternatives, execution priority, performance indicator.
- Follow-ups: evidence for diagnosis, why the selected alternative is first,
  response to committee objection, resources and timeline, field risk.
- Evaluation focus: data analysis, logical structure, feasibility of
  alternatives, clarity of communication.

### 3.4 토론면접

- Main question: present a job issue where two reasonable positions conflict.
- Required shape: conflicting positions, initial position, grounds, response to
  opposing views, agreement plan.
- Follow-ups: evidence for initial position, acceptable and unacceptable parts
  of the opposite view, conflict moderation, required terms of agreement,
  follow-up responsibility.
- Evaluation focus: evidence, listening and interaction, conflict adjustment,
  agreement quality.

### 3.5 인바스켓면접

- Main question: present multiple documents, requests, complaints, deadlines,
  or schedule conflicts under a time limit.
- Required shape: time limit, at least three work items, prioritization,
  report/delegate/direct-process decision, first actions.
- Follow-ups: classification criteria, first and deferred tasks, reporting or
  delegation choice, risk control, records and follow-up checks.
- Evaluation focus: priority judgment, document and request classification,
  time management, risk response.

### 3.6 직무지식면접

- Main question: ask how to apply job knowledge to a concrete task.
- Required shape: procedure, standard or regulation, expected output, exception
  handling, quality check.
- Follow-ups: required criteria, common exceptions, output quality review,
  risk of wrong application, explanation to a new worker.
- Evaluation focus: understanding of procedure and standard, application of
  job knowledge, exception judgment, output quality.

### 3.7 창의적 문제해결력면접

- Main question: present a complex job problem that requires future-oriented
  diagnosis and solution design.
- Required shape: future prediction, problem definition, cause hypothesis,
  at least two creative alternatives, validation method, feasibility,
  decision criteria, execution plan, and performance indicator.
- Follow-ups: assumptions behind the prediction, cause evidence, how to verify
  alternatives, trade-offs, implementation risk, and follow-up indicators.
- Evaluation focus: structured problem definition, creativity grounded in
  evidence, feasibility, decision logic, implementation planning.

## 4. Output Contract

- `type` and `method` must be one of: 경험면접, 상황면접, 발표면접, 토론면접,
  인바스켓면접, 직무지식면접, 창의적 문제해결력면접.
- `question` must contain the method-specific required shape.
- Model-origin questions are preserved only when the main `question` contains
  these method terms directly:
  경험면접 = 경험, 상황, 본인, 행동, 결과;
  상황면접 = 상황, 판단, 기준, 순서, 위험;
  발표면접 = 발표, 진단, 대안, 실행, 성과지표;
  토론면접 = 토론, 충돌, 입장, 반대, 합의;
  인바스켓면접 = 인바스켓, 제한시간, 문서, 우선순위, 보고, 위임, 직접처리;
  직무지식면접 = 절차, 기준, 산출물, 예외상황;
  창의적 문제해결력면접 = 미래예측, 문제정의, 원인가설, 검증, 대안, 실현가능성, 의사결정.
- `follow_ups` should contain three to five items.
- `follow_ups` must be non-duplicative, must deepen the same main question,
  and at least one follow-up must directly repeat a job, NCS detail, or KSA
  term so the probe stays grounded in the target work.
- Method-specific follow-ups must not collapse into generic "please explain
  more" prompts: experience probes situation/role/action/result, situational
  probes facts/criteria/risk/follow-up, presentation probes evidence/alternative
  priority/objection response/metrics, discussion probes position/opposition/
  adjustment/agreement, in-basket probes document classification/priority/
  report-delegate-direct processing, and job-knowledge probes criteria/
  exception/output quality. Creative problem solving probes prediction,
  problem definition, cause hypotheses, alternative validation, feasibility,
  decision criteria, and execution metrics.
- `evaluation_points` should contain four to six assessable points.
- `ncsClCd`, `competency`, `ncs_detail`, `ksa_refs`, and `ksa_evidence` must
  stay aligned to the same NCS competency unit whenever official NCS data is
  available.

## 5. Runtime and repeated-generation contract

- The server audits every adjusted question for method shape, visible KSA
  focus, KSA-type operationalization, and shallow KSA restatement.
- The request carries a bounded, context-specific history of prior questions.
  Exact and near-duplicate candidates are repaired with a different realistic
  constraint before the response is accepted.
- One item-level repair exception must not turn the entire response into an
  HTTP 500. The error is recorded on that item and the result is marked for
  review.
- Candidate count, repair count, repair errors, full-quality failures, and
  plan count gaps must be reported consistently. A `needs_review` result must
  never claim zero unresolved items.
- A repaired set is enriched with official KSA evidence and rerun through the
  full question-quality report before it is returned.

## 6. Source alignment

- NCS describes experience interviews as asking about past experience that
  required the target job ability, situational interviews as observing action
  in a presented situation, presentation interviews as evaluating a
  presentation and Q&A, and discussion interviews as evaluating both the task
  and interaction: <https://www.ncs.go.kr/blind/bl02/RH-103-003-04.scdo>
- NCS interview-question materials are monitored from the official collection:
  <https://www.ncs.go.kr/blind/rh13/bbs_lib_list.do?libDstinCd=49&menuId=MN02020303>
- OPM requires structured questions and common rating standards, and defines
  structured interviews as eliciting past behavior or proposed behavior in a
  hypothetical situation:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/structured-interviews/>
- OPM's work-sample guidance requires tasks to mirror actual work and permits
  scoring observable behavior or task outcomes:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/other-assessment-methods/work-samples-and-simulations/>
- OPM's assessment-center guidance grounds in-basket tasks in memos, messages,
  reports, and articles, and group discussions in a time-limited problem:
  <https://www.opm.gov/policy-data-oversight/assessment-and-selection/other-assessment-methods/assessment-centers/>
