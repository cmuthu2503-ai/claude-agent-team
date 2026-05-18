# Slide 1: Title
Recovering an Empty PRD
Subtitle: A 5-minute intake to unblock the team — no guessing required

---

# Slide 2: Agenda
- The problem: a placeholder PRD
- Why we won't guess
- The intake approach
- The clarifying questions (22, grouped)
- The fill-in PRD template
- Process & next steps

Speaker notes: Set expectations — this is a short walkthrough of how we recover from an empty PRD without slowing the team down or inventing requirements. Should take 10 minutes.

---

# Slide 3: The Problem
- Submitted PRD contains only: *"Test content patch"*
- Zero signal on problem, users, scope, or success
- Acting on it = guaranteed rework
- Title/metadata alone are not enough to reconstruct intent

Speaker notes: Show the actual placeholder string. Make it concrete. Emphasize that this looks like a CMS or git template default that was committed by mistake — common, not catastrophic, but it must be fixed before build starts.

---

# Slide 4: Why We Won't Guess
- Phantom requirements drift from real user needs
- Mid-sprint scope disputes are far more expensive than a 48-hour pause
- Engineering inventing product requirements inverts ownership
- One round-trip with the PO beats a week of Slack archaeology

Speaker notes: This is the cultural point. Pausing to ask is *not* gatekeeping — it's the cheapest possible insurance against building the wrong thing. Reference Cagan/Atlassian best practice.

---

# Slide 5: The Approach — Batched, Structured Intake
- One artifact, not a thread of questions
- Structured fields, not open prose
- Required (✱) vs. optional, sized to the feature
- 48-hour turnaround + 15-minute review call

Speaker notes: Explain the design choices. Batching respects the PO's time. Structured fields force sharper answers than "please clarify." Required vs. optional means a 1-day bug fix doesn't need a market-sizing section.

---

# Slide 6: Clarifying Questions — The Six Sections
- Problem & Context (Q1–4)
- Users & Roles (Q5–8)
- Goals & Scope (Q9–13)
- Success Criteria & Metrics (Q14–16)
- Constraints (Q17–20)
- Risks & Open Questions (Q21–22)

Speaker notes: 22 questions total, but only ~10 are blocking (✱). These six sections map to the standard *why / who / what / how-we-know-it-worked / what-we-won't-do* decomposition used in every major PM framework.

---

# Slide 7: The Highest-Value Questions
- ✱ What problem does this solve? (Q1)
- ✱ Who is the primary user — specific role, not "users"? (Q5)
- ✱ Top 1–3 outcomes? (Q9)
- ✱ What is explicitly OUT of scope? (Q12)
- ✱ How do we measure success at 30/60/90 days? (Q14)

Speaker notes: If the PO answers only five questions, these are the five. Out-of-scope (Q12) is the unsung hero — it prevents scope creep more reliably than any in-scope list.

---

# Slide 8: The Fill-In PRD Template
- 10 sections; completed form *becomes* the canonical PRD
- Header: author, date, status, target release, squad
- Tables for Users, Success Metrics, Risks — forces specificity
- Approval checklist (PO, Eng, Design, Security/Legal)

Speaker notes: Walk through the template structure on screen if possible. The point is that the PO isn't writing a document from scratch — they're filling fields. Side-effect: we get a durable, reviewable artifact for free.

---

# Slide 9: Risks & Mitigations
- "Feels bureaucratic" → frame as 5-min form, pre-fill what you can
- "Too heavy for small work" → ✱ vs. optional fields
- "Replaces conversation" → schedule the 15-min review call
- "Real PRD exists elsewhere" → check version history first

Speaker notes: Last bullet is critical — before sending the questionnaire, always check git history / Confluence / Notion. The placeholder may be an accidental overwrite of a real draft, and you can save the PO an hour.

---

# Slide 10: When NOT to Use This
- Trivial copy change or 1-line config tweak → Slack the PO instead
- Contractual hard deadline + PO unavailable → escalate to delegate
- Large/strategic feature with many unknowns → upgrade to a discovery workshop

Speaker notes: The template is a default, not a law. Engineering judgment still applies. Three clear escape hatches keep the process from feeling rigid.

---

# Slide 11: Next Steps & Owners
- **Today** — Research/PM lead sends Deliverable A + B to PO (CC eng lead)
- **Today** — Eng lead moves ticket to *Blocked: Awaiting PRD*
- **Today** — Eng lead checks version history for a recoverable prior draft
- **Within 2 days** — PO returns completed template
- **On return** — 15-min review call to pressure-test weak answers

Speaker notes: Clear owners, clear timing. No ambiguity about who does what. The version-history check is cheap and may make the whole questionnaire unnecessary.

---

# Slide 12: Key Takeaways
- A placeholder PRD is recoverable in one round-trip, not a week
- Don't guess — ask, in a structured form
- The completed template *is* the PRD
- Frame it as enabling, not gatekeeping

Speaker notes: Close with the cultural point. This process exists to get the team building the right thing on Monday — not to slow anyone down. Questions?
