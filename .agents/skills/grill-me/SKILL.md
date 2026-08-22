---
name: grill-me
description: "Pressure-test an implementation plan through a structured decision interview. Use only when the user explicitly says grill me, stress-test this plan, interview me about the plan, or makes an unmistakably equivalent request. Never invoke implicitly."
---

# Grill Me

## Invocation — read this first

**This skill is user-invoked only.** Do not start grilling on your own, do not offer to grill
after every plan draft, and do not treat an ambiguous plan as an invitation. Wait for an explicit
request from the user in plain chat — "grill me", "stress-test this", "interview me about the
plan", or anything unmistakably equivalent. Until that message arrives, this text is inert; plan
normally.

The user invokes this with `$grill-me` or an explicit prose request. Mention it once, if useful,
then drop it.

When the user stops the interview — explicitly, or by asking you to just write the plan — stop
immediately, fold in what was resolved, and leave the rest open.

## Purpose

Structured design pressure-testing. The goal is **shared understanding**, reached by resolving
each branch of the decision tree, not a satisfying interrogation. You are looking for the
decisions the plan is quietly assuming, the ones that will be expensive to reverse, and the ones
where you and the user disagree without knowing it yet.

## Inputs

- The plan under discussion.
- An active `Context/Ledger/task-*.md`, when one exists.
- **The codebase.** If a question can be answered by reading the code, read the code. Asking the
  user something the repo already answers wastes their attention and teaches them the interview
  is theatre.

## Procedure

1. **Map the decision tree** silently first: which choices does this plan depend on, and which
   of those depend on each other? Order them so that upstream decisions come first — resolving a
   detail before its parent is wasted work.
2. **Ask exactly one question at a time.** One pointed question, then wait. Never a numbered
   list of five. Never a question with three sub-questions bolted on.
3. **Offer your recommended answer with every question**, plus the one-line reason and the cost
   of the alternative. The user should be able to reply "yes" and have that be a good decision.
   A question without a recommendation offloads your job onto them.
4. **Follow each answer into its next dependency or tradeoff.** The answer usually creates the
   next question — take that branch to resolution before returning to breadth. Say when you are
   switching branches so the user can follow the shape of the interview.
5. **Push back once when an answer looks wrong**, with the concrete consequence you foresee.
   Then accept the decision and move on. You are pressure-testing, not filibustering; the user
   decides.
6. **Close every branch.** A branch is resolved when the decision is stated in a form that
   constrains implementation ("retry with exponential backoff, cap 30s, give up after 5") rather
   than a direction ("make retries better"). Track the open branches and say what remains.
7. **Record as you go.** Track each material resolution in the conversation. When exactly one
   active `Context/Ledger/task-*.md` exists, append the decision, rejected alternative, and reason
   under its `Decisions` section with a normal file edit. Never invent a ledger or choose between
   multiple active ledgers without user direction.
8. **Fold the resolutions into the plan.** When the interview ends, restate the affected plan
   items so the plan itself carries the decisions. When an active ledger was found, append a new,
   superseding entry under `Approved Plan`; do not rewrite earlier entries. Explicitly list any
   unresolved branch and what it blocks.

## Question quality

Good: "The plan retries failed syncs. `src/scheduler/queue.ts` assumes one consumer — a retry
that re-enqueues in parallel breaks ordering. Retry in place and block the queue, or re-enqueue
and accept out-of-order delivery? I recommend retry in place: ordering is a contract other
callers rely on, and the throughput cost only shows above ~100 jobs/min."

Bad: "Have you considered error handling?" — no specific decision, no recommendation, no
grounding in the code.

Ask about: reversibility, failure modes, what happens under concurrency, what happens on the
second run, who owns the new state, what the migration/rollback looks like, what the plan does
*not* cover, and which existing invariant this change is nearest to breaking.

## Outputs

- One recorded decision per resolved branch (decision, rejected alternative, reason).
- A superseding plan in the conversation and, when an active ledger exists, under its
  `Approved Plan` section.
- A short closing message: decisions resolved, branches left open, and what each open branch
  blocks.

## Rules

- Never self-activate. Explicit user request only.
- One question per message. No exceptions.
- Every question carries a recommendation.
- Explore the codebase rather than asking, whenever the code can answer.
- Never keep grilling after the user says stop.
- Never let the interview replace the plan: the deliverable is the updated plan plus the
  `Decisions` entries.
