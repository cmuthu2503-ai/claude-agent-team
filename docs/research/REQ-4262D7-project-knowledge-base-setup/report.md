# Project Knowledge Base Setup
## A 2026 Research Report on Platforms, Architecture & Governance

**Prepared:** May 2026
**Audience:** Project leads, engineering managers, operations leaders
**Status:** Final

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Scope & Background](#2-scope--background)
3. [Key Findings](#3-key-findings)
4. [Analysis: Pros, Cons & Alternatives](#4-analysis-pros-cons--alternatives)
5. [Platform Comparison](#5-platform-comparison)
6. [Recommendation](#6-recommendation)
7. [Next Steps & Rollout Plan](#7-next-steps--rollout-plan)
8. [Items to Verify Independently](#8-items-to-verify-independently)
9. [Sources](#9-sources)

---

## 1. Executive Summary

A project knowledge base (KB) in 2026 should be **task-organized, AI-ready, and governed by a named owner** — not a folder dump. For most small-to-mid projects, **Notion or Confluence is the right starting platform**, with the choice driven by ecosystem:

- **Atlassian/Jira-heavy team →** Confluence (Rovo AI bundled, native Jira links)
- **Cross-functional, non-technical team →** Notion (better editor, richer templates)

The single biggest predictor of success is **not the tool** but the *information architecture* — categories organized by jobs-to-be-done, no more than three levels deep — combined with a maintenance cadence and assigned ownership.

---

## 2. Scope & Background

**Research scope covers:**

- What a project KB is and why teams need one
- Top platform options available in 2026
- Structural and information-architecture best practices
- AI-readiness for RAG and agentic consumption
- Governance, ownership, and lifecycle

**Out of scope:** Customer-support-only platforms (Stonly, eGain, Zendesk Guide), since "project KB" implies internal team use.

### Why it matters

Most KBs fail within 12 months — content goes stale, search breaks, and employees stop trusting it. A January 2026 Stravito study notes that teams that mirror their org chart in their KB see adoption collapse within two quarters. Meanwhile, AI agents (Copilot, Claude, internal RAG bots) increasingly **consume** the KB on behalf of humans, so structural quality directly determines AI answer quality (Rezolve.ai, Nov 2025).

---

## 3. Key Findings

| # | Finding | Reasoning | Confidence |
|---|---------|-----------|------------|
| 1 | Platform choice matters less than information architecture | Tool-agnostic guides (eesel, Stravito) converge on the same failure mode: KBs collapse when organized by team or file type instead of by user task. Switching tools never fixes bad structure. | High |
| 2 | Confluence offers better AI value at the price point (~$5.42/user with Rovo AI bundled) than Notion (~$10/seat base + $10/seat AI add-on) | Per Docsie's 2026 pricing analysis. For a 20-person team: Notion w/ AI ≈ $4,800/yr; Confluence ≈ $1,300/yr. | High |
| 3 | KBs must now be "AI-ready" — modular, tagged, well-chunked — because GenAI agents read them | Rezolve.ai (Nov 2025) and Front (2026) both flag unstructured prose as a top cause of RAG hallucinations. Articles should be atomic and machine-readable. | High |
| 4 | Notion has crossed 100M users and is the default for cross-functional project teams; Obsidian remains niche | Per tech-insider.org and atlasworkspace.ai (May 2026). Obsidian (2M+ active) lacks real-time multi-user collaboration. | High |
| 5 | Consensus IA pattern: 5–8 top-level categories, max 3 levels deep, "3-click rule" | Cited across eesel.ai, Stravito, and r/msp practitioner threads. One r/msp user reported a 40% duplicate-doc reduction after switching from category-based to workflow-based structure. | Medium |
| 6 | Named ownership is the single biggest predictor of KB freshness | Bloomfire (Jan 2026) and Help Scout both identify shared ownership as the root cause of stale content. A KB without an assigned curator decays within ~6 months. | High |
| 7 | Start small: 10–20 well-written articles beat 80 thin ones at launch | Sparse but accurate KBs build trust; bloated KBs erode it. Time-to-launch for an MVP KB: 1–2 weeks. | Medium |

---

## 4. Analysis: Pros, Cons & Alternatives

### 4.1 Pros of a dedicated project KB

- **Onboarding speed.** New project members ramp up to 50% faster when canonical decisions and docs live in one place (eGain 2026: "50% improvement in speed to competence").
- **Decision audit trail.** Architecture Decision Records (ADRs), meeting notes, and rationale captured in-place reduce "why did we do this?" churn months later.
- **AI multiplier.** A clean KB instantly improves any RAG/agent tooling layered on top (Copilot, Glean, custom bots).
- **Async coordination.** Reduces interrupt-driven Slack pings. Front's 2026 research shows B2B teams spend 3 hours coordinating per 1 hour solving.

### 4.2 Cons & risks

| Risk | Mitigation |
|------|------------|
| **Decay** — without an owner + quarterly review, the KB becomes a "digital graveyard" | Assign one named curator per category; calendar quarterly audits |
| **Tool sprawl** — adding a KB on top of Drive + Slack + Jira creates *more* places to look | Designate the KB as single source of truth for one content type (e.g., "decisions and runbooks live here, chat stays in Slack") |
| **Per-user cost compounds** — a 50-person Notion Business + AI deployment runs ~$12k/yr | Use Confluence if Atlassian-licensed already; or restrict editor seats and use free read-only access |
| **Migration lock-in** — moving a 2,000-page Notion workspace = 10–20 hours of manual work (tech-insider.org, Mar 2026) | Prefer Markdown-exportable tools; avoid heavy use of proprietary database features |

### 4.3 Alternatives Considered

- **Obsidian + Git** — Excellent for individuals or small technical teams (free, local-first, Markdown). Excluded for most project teams because real-time collaboration is weak.
- **GitHub / GitLab Wiki** — Good for code-adjacent projects but poor for non-engineers.
- **SharePoint** — Default if you're already Microsoft 365-licensed; included in M365 Business plans. Search is improving with Copilot but UX lags Notion and Confluence.
- **Google Drive folders** — Cheap, but breaks down past ~20 documents — no search analytics, no versioning workflows.

---

## 5. Platform Comparison

| Criteria | Weight | Notion | Confluence | Obsidian | SharePoint |
|----------|:------:|:------:|:----------:|:--------:|:----------:|
| Ease of use (non-technical) | High | 9/10 — block editor, intuitive | 7/10 — feels enterprise | 5/10 — Markdown curve | 6/10 — clunky but familiar |
| Cost at 20 seats/yr | High | 7/10 — ~$2.4k base + ~$2.4k AI | 9/10 — ~$1.3k w/ Rovo AI | 10/10 — free (+$4/mo sync) | 9/10 — bundled in M365 |
| AI / agentic readiness | High | 8/10 — strong AI Q&A | 9/10 — Rovo AI bundled, 20+ agents | 5/10 — plugin-dependent | 8/10 — Copilot integration |
| Collaboration / real-time | High | 9/10 | 8/10 | 4/10 — local-first | 7/10 |
| Integration with dev tools | Medium | 7/10 | 10/10 — Jira native | 6/10 | 6/10 |
| Search quality | High | 8/10 | 8/10 | 7/10 (with plugins) | 7/10 (improving) |
| Export / lock-in risk | Medium | 6/10 — proprietary blocks | 7/10 | 10/10 — plain Markdown | 6/10 |
| **Weighted Score** | | **7.8/10** | **8.3/10** | **6.3/10** | **7.0/10** |

---

## 6. Recommendation

> **Use Confluence if your team already has Atlassian/Jira; otherwise use Notion.**

Confluence wins on weighted score because Rovo AI is bundled (significant cost savings) and Jira integration is native — invaluable for project work where docs link to tickets. Notion wins for cross-functional teams that include non-technical members (marketing, ops, design) because the editor is meaningfully more pleasant and the template ecosystem (20,000+) accelerates setup.

### 6.1 Regardless of tool, invest first in:

1. **A jobs-to-be-done category structure** — 5–8 top categories, max 3 levels deep
2. **An article template** — title-as-question, intro, prerequisites, numbered steps, owner, last-reviewed date
3. **One named curator** with a quarterly review on the calendar

### 6.2 When NOT to follow this recommendation

| Situation | Better choice |
|-----------|---------------|
| Solo, privacy-critical, or offline work | **Obsidian** — free, local, Markdown-portable |
| Heavy Microsoft 365 shop with Copilot licenses | **SharePoint + OneNote** — avoid adding a third tool |
| Customer-facing KB (needs custom domain, multi-tenant) | **Document360, Zendesk Guide, or Stonly** |
| Engineering-only project with docs-as-code culture | **GitHub Wiki** or **MkDocs / Docusaurus** in the repo |

---

## 7. Next Steps & Rollout Plan

| Phase | Owner | Action |
|-------|-------|--------|
| **Week 1 — Define scope** | Project lead | Decide internal vs. external audience; pick 1–2 success metrics (e.g., "new hire onboarded in <5 days without 1:1," "RFC questions answered in KB rather than Slack") |
| **Week 1 — Audit existing content** | Project lead + 1 SME | Pull existing Google Docs, Slack pinned items, meeting notes from the last 90 days. Tag by topic to find the 10–20 highest-frequency questions |
| **Week 2 — Pick platform & set up IA** | Project lead | Run the comparison table against your team's reality. Create 5–8 top-level categories organized by *task*, not by team |
| **Week 2–3 — Write 10–20 seed articles** | SMEs, reviewed by curator | Use a consistent template. Get SME review before publishing |
| **Week 3 — Launch & promote** | Project lead | Announce in Slack/email; link from onboarding docs; pin in project channels. Treat launch as marketing |
| **Ongoing — Quarterly review** | Named curator | Calendar invite; review every article; archive stale ones; draft articles for any recurring question that hit Slack instead of the KB |

---

## 8. Items to Verify Independently

- **Current pricing** for Notion / Confluence / Obsidian Sync — vendors change tiers frequently. Confirm at notion.so/pricing and atlassian.com/software/confluence/pricing before procurement.
- **Existing license bundle** — check if your org already has Confluence (via Jira) or SharePoint (via M365). Buying duplicates is the most common avoidable cost.
- **AI/RAG integration roadmap** — if you plan to layer an internal AI agent on the KB, verify the platform's API limits and embedding-export support. These vary and aren't always documented on pricing pages.
- **Data residency / compliance** — if working with regulated data (health, finance, EU PII), confirm data-residency options independently with the vendor's security team.

---

## 9. Sources

Live web search performed May 2026. Primary sources:

- eesel.ai — Knowledge base setup guide (May 2026)
- Stravito — KB adoption study (Jan 2026)
- Stonly — KB best practices (Feb 2026)
- Docsie — Confluence vs Notion pricing comparison (2026)
- Atlas Workspace — Notion vs Obsidian (May 2026)
- Front — B2B coordination overhead research (2026)
- Bloomfire — KB governance (Jan 2026)
- Rezolve.ai — AI-ready KB structure (Nov 2025)
- Gartner Peer Insights, Freshworks, Help Scout, tech-insider.org
