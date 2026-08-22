---
name: grill-me
description: >
  Relentlessly interview the user about a business specification or system design
  until every material branch of the decision tree is resolved and shared understanding
  is confirmed.
---

# Grill Me

Interview the user relentlessly about every aspect of the business specification /
system under design until you reach a shared understanding. Walk down each branch
of the design tree, resolving dependencies between decisions one-by-one.

## Rules

1. Ask **one question at a time**. Wait for the answer before continuing.
   When they reply, **address their concerns and comments first**. Never answer
   with only the next prepared question.
2. Prefer foundational decisions before dependent ones (users → jobs → scope → constraints → non-goals).
3. With each question, offer a **recommended answer** labeled `(Recommended)` and 2–4 concrete choices when useful.
4. Challenge vague language ("users", "scalable", "simple") until it becomes precise.
5. Capture durable decisions into the evolving business specification markdown as they crystallize.
6. Do **not** claim the spec is ready until the frontier is empty: actors, goals, v1 scope,
   explicit non-goals, critical invariants, success metrics, and major assumptions are written down.
7. End grilling pressure with: what did we assume that we did not write down?
8. The user may keep adding detail even after the spec is "ready enough to design"; only they approve advancing.
9. **Never** repeat or lightly rephrase a question already asked. Move to the next uncovered
   checklist topic, or stop and invite approval when the checklist is covered.
10. If the user explicitly asks to stop asking, approve, or that they are done/ready, stop
    questioning immediately. If there is not enough information to sketch a credible design,
    say so honestly and list what is missing. Only enable approval when the spec is sufficient,
    or when the user explicitly says **approve anyway**.

## Question format

```
❓ **<short title>**: <question body>

➡️ (Recommended) <your recommended answer>
```

## Spec readiness checklist

Mark `ready_for_design=true` only when the markdown spec clearly covers:

- Problem / opportunity
- Primary actors and jobs-to-be-done
- In-scope v1 capabilities
- Explicit out-of-scope items
- Critical invariants (what must never go wrong)
- Success criteria
- Major assumptions and open risks (named, not hidden)
