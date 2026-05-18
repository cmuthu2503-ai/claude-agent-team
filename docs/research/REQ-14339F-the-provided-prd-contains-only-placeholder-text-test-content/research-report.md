## Research Report: Recovering an Empty PRD — Clarifying Questions & Intake Template for Product Owner

> **Sources:** This is a process/template-design task drawing on established PRD best practices (Marty Cagan, Lenny Rachitsky, Atlassian, ProductPlan). No live web search required — the deliverable is a reusable artifact, not a market scan.

### 1. Executive Summary
The submitted PRD contains only placeholder text ("Test content patch") and cannot be used to drive design, estimation, or implementation. The correct response is **not** to infer requirements, but to return a structured intake template to the product owner with targeted clarifying questions covering problem, users, scope, success metrics, and constraints. A ready-to-send template and question bank are provided below.

### 2. Scope & Background
**Research scope:** Design a (a) clarifying-questions checklist and (b) fill-in PRD template that the product owner can complete to convert the empty document into an actionable spec. Excludes: guessing at the feature's purpose, doing competitive research on an unknown feature.

**Why it matters:** Proceeding on a placeholder PRD risks building the wrong thing, wasted engineering cycles, and ambiguous acceptance criteria. A short, structured intake unblocks the team within one product-owner working session rather than triggering rework after build.

### 3. Key Findings

| # | Finding | Reasoning | Confidence |
|---|---------|-----------|------------|
| 1 | The PRD is non-actionable in its current state | "Test content patch" is a placeholder string typically left by a CMS or git template; it contains zero requirement signal. No team member can derive scope, users, or acceptance criteria from it. | High |
| 2 | Do not attempt to reconstruct intent from the title or metadata alone | Inferring requirements without product-owner confirmation creates phantom requirements that drift from real user needs, leading to scope disputes mid-sprint. Better to pause and ask. | High |
| 3 | A structured intake template outperforms open-ended "please clarify" requests | Product owners under time pressure default to vague prose unless given specific fields. Forced-choice fields (e.g., user role dropdown, must-have vs. nice-to-have) yield faster, sharper answers. | High |
| 4 | The minimum viable PRD has 6 sections: Problem, Users, Goals, Scope, Success Metrics, Constraints | These map to the standard "why / who / what / how-we-know-it-worked / what-we-won't-do" decomposition used across major PM frameworks (Cagan's INVEST, Amazon's PR/FAQ, Atlassian PRD template). Missing any one leaves a known failure mode. | High |
| 5 | Clarifying questions should be batched, not drip-fed | Asynchronous back-and-forth across many messages stretches clarification over days. A single consolidated questionnaire respects the PO's time and produces a complete artifact in one pass. | Medium |

### 4. Analysis

**Pros of the structured-intake approach:**
- **Unblocks the team in one round-trip** — PO fills the template once, work can start.
- **Creates an audit trail** — completed template becomes the canonical PRD, no ambiguity about "what did we agree to?"
- **Surfaces hidden assumptions early** — forcing the PO to write success metrics often reveals the feature isn't fully thought through, *before* engineering invests.

**Cons / risks:**
- **PO may perceive it as bureaucratic pushback.** *Mitigation:* frame as "5-minute form to unblock your team," and pre-fill any fields you can infer (e.g., target release, owning squad).
- **Template can feel heavy for tiny features.** *Mitigation:* mark fields as Required (✱) vs. Optional, so a 1-day bug fix doesn't need a market-sizing section.
- **Risk of treating the template as a substitute for conversation.** *Mitigation:* schedule a 15-minute review call after the PO submits, to probe weak answers.

**Alternatives Considered:**
- *Reject the PRD outright and ask PO to resubmit.* Excluded — provides no guidance on what "good" looks like, likely to produce another low-quality draft.
- *Have engineering draft the PRD and ask PO to approve.* Excluded — inverts ownership; engineering shouldn't be inventing product requirements.
- *Schedule a discovery workshop instead of a written template.* Reasonable for large features, but overkill if the feature is small; recommend the template first, escalate to workshop if answers reveal complexity.

### 5. Deliverable A — Clarifying Questions for the Product Owner

Send these grouped by section. ✱ = blocking (cannot start work without).

**Problem & Context**
1. ✱ What user or business problem does this feature solve? (1–3 sentences)
2. ✱ What evidence do we have this problem is real? (support tickets, user research, revenue data, etc.)
3. What happens if we do *nothing*?
4. Is this net-new functionality, an enhancement, or a fix?

**Users & Roles**
5. ✱ Who is the primary user? (specific persona or role, not "users")
6. Are there secondary users or affected roles (e.g., admins, support staff)?
7. ✱ What permissions / access level does each role need for this feature?
8. Approximately how many users are in each role today?

**Goals & Scope**
9. ✱ What are the top 1–3 outcomes this feature must achieve?
10. ✱ List the **must-have** capabilities for v1.
11. List **nice-to-have** capabilities explicitly deferred to v2+.
12. ✱ What is explicitly **out of scope**? (this prevents scope creep)
13. Are there dependencies on other teams, systems, or third-party APIs?

**Success Criteria & Metrics**
14. ✱ How will we know this feature succeeded 30 / 60 / 90 days post-launch? (quantitative if possible)
15. What is the target baseline vs. goal? (e.g., "task completion 40% → 70%")
16. What is the *failure* threshold that would trigger a rollback or rethink?

**Constraints**
17. ✱ Target launch date or release window? Is the date hard or soft?
18. Budget, headcount, or compute constraints?
19. ✱ Compliance, security, accessibility (WCAG), or data-residency requirements?
20. Known technical constraints (must run on legacy stack X, must integrate with system Y)?

**Risks & Open Questions**
21. What's the riskiest assumption in this feature?
22. What questions do *you* (the PO) still have unanswered?

### 6. Deliverable B — Fill-In PRD Template

```markdown
# PRD: [Feature Name]
**Author (PO):** ___________  **Date:** ___________  **Status:** Draft / Review / Approved
**Target Release:** ___________  **Engineering Squad:** ___________

## 1. Problem Statement  ✱
[1–3 sentences. What user/business problem are we solving and why now?]

## 2. Evidence & Context  ✱
- Supporting data:
- Linked research / tickets / dashboards:
- Cost of inaction:

## 3. Users & Roles  ✱
| Role | Description | Approx. # Users | Permissions Needed |
|------|-------------|-----------------|--------------------|
| Primary: | | | |
| Secondary: | | | |

## 4. Goals (Outcomes)  ✱
1.
2.
3.

## 5. Scope
### In Scope (Must-Have for v1) ✱
- [ ]
- [ ]

### Nice-to-Have (Deferred)
- [ ]

### Out of Scope ✱
- [ ]

## 6. Success Metrics ✱
| Metric | Baseline | 30-day Target | 90-day Target | Failure Threshold |
|--------|----------|---------------|---------------|-------------------|
| | | | | |

## 7. Constraints
- **Timeline:** ___________  (Hard / Soft)
- **Compliance / Security / A11y:** ___________
- **Technical:** ___________
- **Budget / Resourcing:** ___________

## 8. Dependencies
- Internal teams:
- External systems / APIs:
- Blocking decisions:

## 9. Risks & Open Questions
| Risk / Question | Owner | Resolution Needed By |
|-----------------|-------|----------------------|
| | | |

## 10. Approvals
- [ ] Product Owner
- [ ] Engineering Lead
- [ ] Design Lead
- [ ] (If applicable) Security / Legal
```

### 7. Recommendation
**Send the product owner the consolidated questionnaire (Deliverable A) along with the fill-in template (Deliverable B), with a 48-hour turnaround request and an offer of a 15-minute working session.** This approach respects the PO's time (single artifact, structured fields), creates a durable PRD as a side-effect of answering, and avoids the failure mode of engineering inventing requirements. Frame it as enabling, not gatekeeping: "Here's a 5-minute form so your team can start on Monday."

**When NOT to follow this recommendation:**
- If the feature is genuinely trivial (e.g., a copy change or a 1-line config tweak), the template is overkill — a Slack confirmation with the PO is sufficient.
- If the PO is unavailable and there is a contractual hard deadline, escalate to their delegate or product leadership rather than waiting.
- If the placeholder PRD was committed by mistake and a real PRD already exists elsewhere (e.g., Confluence), find that document first before sending a questionnaire.

### 8. Next Steps
- **Owner: Research/PM lead** — Send Deliverable A + B to the product owner today; CC engineering lead. Request response within 2 business days.
- **Owner: Engineering lead** — Place current ticket/epic in `Blocked: Awaiting PRD` state; do not begin estimation.
- **Owner: Research/PM lead** — Schedule a 15-minute review call for the day the completed template is returned, to pressure-test weak answers (especially success metrics and out-of-scope).
- **Owner: Product owner** — Confirm whether the "Test content patch" placeholder was committed in error and whether a real PRD draft exists elsewhere.
- **To verify independently:** Check the document's version history / commit log to identify who last edited it and whether prior content was overwritten — the real PRD may be recoverable without a full re-intake.