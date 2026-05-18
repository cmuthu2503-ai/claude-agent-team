# Slide 1: Title
What Is a "Content Patch"?
Subtitle: Disambiguating an internal term in the Agent Team platform

---

# Slide 2: Agenda
- The question and why it matters
- What we found (and didn't find)
- Three candidate interpretations
- Weighted comparison
- Recommendation and verification plan

Speaker notes: We're going to spend about ten minutes pinning down what "content patch" actually means on this platform, because the answer determines who picks up the ticket and where the change lands.

---

# Slide 3: The Question
- "Content patch" appears in tickets, PRs, and team chat
- Three candidate meanings on the table:
  - Backend content update
  - Frontend copy change
  - DB seed update
- No shared written definition exists

Speaker notes: This came up because the term is being used without a precise meaning. Different team members may be interpreting it differently, which creates routing and QA risk.

---

# Slide 4: Why It Matters
- Wrong interpretation → wrong file tree, wrong reviewer, wrong QA surface
- Sprint planning misfires when scope is unclear
- Onboarding friction for new engineers
- One-sentence glossary fix prevents recurrence

Speaker notes: This is a small ambiguity with an outsized impact on velocity. The cost of fixing it is essentially zero; the cost of leaving it is a steady drip of misrouted work.

---

# Slide 5: What We Found
- Zero public documentation for the term
- It is internal jargon, not industry standard
- Standard convention: "content" = non-code (copy, config, data)
- "Patch" = small, targeted update
- Strong inference: not a logic/code change

Speaker notes: Web search returned nothing. That's expected — the platform is internal. But naming conventions across the industry are consistent enough to rule out one interpretation: it's not a code change.

---

# Slide 6: The Three Candidates
- **(A) Backend content update** — file-based prompts/configs in the repo
- **(B) Frontend copy change** — button labels, headings, UI strings
- **(C) DB seed / prompt-table update** — rows in a prompts or templates table
- These are not mutually exclusive in general — only one is correct here

Speaker notes: The same business intent — "fix the wording" — can map to any of these three depending on where strings live in your stack. The right answer depends on the Agent Team platform's specific architecture.

---

# Slide 7: Weighted Comparison
- (A) Backend file-based content — **6.6 / 10**
- (B) Frontend copy change — **4.6 / 10**
- (C) DB seed / prompt-table — **8.7 / 10**
- Scored on: domain fit, distinctness of term, frequency, ops model

Speaker notes: Option C wins on every dimension. It best explains why the team uses a distinct term — because seeds ship on a different cadence than code deploys, which is exactly the kind of distinction that earns its own vocabulary.

---

# Slide 8: Why Option C Is the Best Prior
- Agent platforms = prompt-driven
- Prompts typically stored as DB rows (LangSmith, Langfuse, Vellum patterns)
- Allows hot-reload without redeploy
- PM/editor can ship via admin UI or seed script
- Distinct term → distinct release cadence

Speaker notes: When the dominant content artifact is prompts, and prompts live in the database, you need a non-code change path. That path needs a name. "Content patch" is exactly the kind of name a team would invent for it.

---

# Slide 9: But This Is a Hypothesis
- No public source confirms the meaning
- Could legitimately be A or B in this specific codebase
- Acting on assumption = risk
- Verification is fast and cheap

Speaker notes: I want to be clear: this is a prior, not a conclusion. We should not put it in the glossary based on this analysis alone. We confirm first.

---

# Slide 10: Verification Plan
- `grep -ri "content patch"` across repo, docs, PR titles
- Look for tell-tale dirs: `prompts/`, `seeds/`, `migrations/`, `i18n/`
- Inspect file diffs of last 3–5 "content patch" PRs:
  - `.sql` / seeds → **Option C**
  - `.md` / `.yaml` in content dir → **Option A**
  - `.tsx` / locale files → **Option B**

Speaker notes: A definitive answer is usually one grep away. The file extensions in recent PRs labeled "content patch" will conclusively settle this in under an hour.

---

# Slide 11: Recommendation
- **Working hypothesis:** content patch = DB seed / prompt-table update
- **Secondary:** file-based backend content update
- **Action:** verify via repo grep before acting
- **Then:** add one sentence to `CONTRIBUTING.md` or team glossary
- **Outcome:** zero recurrence of this ambiguity

Speaker notes: Treat C as the default mental model, verify within a day, and codify the result. The whole cycle should take less than an hour and permanently retire this question.

---

# Slide 12: Q&A
- Working hypothesis: DB seed / prompt-table update (Option C)
- Verify with grep + recent PR diffs
- Codify in glossary once confirmed
- Questions?

Speaker notes: Happy to walk through the comparison matrix or the verification plan in more detail. The main ask is: someone with repo access runs the grep today.
