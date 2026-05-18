I'll research how to set up a project knowledge base in 2026, covering current tools, approaches, and best practices.

I have enough fresh data to build a comprehensive report. Let me produce the structured assessment.

## Research Report: Project Knowledge Base Setup

> **Sources:** Live web search performed May 2026. Primary sources: eesel.ai (May 2026 guide), Stravito (Jan 2026), Stonly (Feb 2026), Docsie (2026 Confluence/Notion pricing), Atlas Workspace (May 2026 Notion vs Obsidian), Front, Bloomfire, Rezolve.ai, Gartner Peer Insights, Freshworks.

### 1. Executive Summary
A project knowledge base (KB) in 2026 should be **task-organized, AI-ready, and governed by a named owner** — not a folder dump. For most small-to-mid projects, **Notion or Confluence is the right starting platform**, with the choice driven by ecosystem (Atlassian-heavy → Confluence; flexible/non-technical → Notion). The single biggest predictor of success is not the tool but the *information architecture* (organized by jobs-to-be-done, max 3 levels deep) plus a maintenance cadence with assigned ownership.

### 2. Scope & Background
**Research scope:** Covers (a) what a project KB is, (b) top platform options in 2026, (c) structural/IA best practices, (d) AI-readiness, and (e) governance. Excludes deep customer-support-only platforms (Stonly, eGain, Zendesk Guide) since "project KB" implies internal team use.

**Why it matters:** Most KBs fail within 12 months — content goes stale, search breaks, employees stop trusting it. A 2026 Stravito study notes teams that mirror org charts in their KB see adoption collapse. Meanwhile, AI agents (Copilot, Claude, internal RAG bots) increasingly *consume* the KB, so structure quality directly determines AI answer quality (Rezolve.ai, Nov 2025).

### 3. Key Findings

| # | Finding | Reasoning | Confidence |
|---|---------|-----------|------------|
| 1 | The platform choice matters less than the information architecture | Multiple 2026 guides (eesel, Stravito) converge on the same failure mode: tool-agnostic, KBs collapse when organized by team/file-type instead of by user task. Switching tools doesn't fix bad structure. | High |
| 2 | Confluence offers better AI value at the price point ($5.42/user with Rovo AI bundled) than Notion ($10/seat base + $10/seat AI add-on) | Per Docsie's 2026 pricing analysis [source: docsie.io/blog/articles/confluence-vs-notion-pricing-comparison-2026/]. For a 20-person project team, Notion w/ AI = ~$4,800/yr vs Confluence ~$1,300/yr. | High |
| 3 | KBs must now be "AI-ready" — modular, with metadata and clear chunking — because GenAI/agentic systems read them | Rezolve.ai (Nov 2025) and Front (2026) both note that unstructured prose causes RAG hallucinations. Articles should be atomic, tagged, and machine-readable. | High |
| 4 | Notion has crossed 100M users and is the default for cross-functional/non-engineering project teams in 2026; Obsidian remains niche for individuals/privacy-focused users | Per tech-insider.org and atlasworkspace.ai (May 2026). Obsidian (2M+ active) lacks real-time multi-user collaboration needed for project work. | High |
| 5 | The "five-to-eight top-level categories, max 3 levels deep, 3-click rule" is the consensus IA pattern | Cited across eesel.ai, Stravito, and Reddit r/msp practitioner threads. One r/msp user reported 40% duplicate-doc reduction after switching from category-based to workflow-based structure. | Medium |
| 6 | Named ownership is the single biggest predictor of KB freshness | Bloomfire (Jan 2026) and Help Scout both flag shared ownership as the root cause of stale content. A KB without an assigned curator decays within ~6 months. | High |
| 7 | Start small: 10–20 well-written articles beat 80 thin ones at launch | Sparse, accurate KB builds trust; bloated KB erodes it. Time-to-launch for a minimum viable KB: 1–2 weeks. | Medium |

### 4. Analysis

**Pros of setting up a dedicated project KB:**
- **Onboarding speed:** New project members ramp 50% faster when canonical decisions/docs are in one place (eGain 2026 cites "50% improvement in speed to competence").
- **Decision audit trail:** Architecture Decision Records (ADRs), meeting notes, and rationale captured in-place reduce "why did we do this?" churn.
- **AI multiplier:** A clean KB instantly improves any RAG/agent tooling layered on top (Copilot, Glean, custom bots).
- **Async coordination:** Reduces interrupt-driven Slack pings — Front's 2026 research shows B2B teams spend 3 hrs coordinating per 1 hr solving.

**Cons & risks:**
- **Decay risk:** Without owner + quarterly review cadence, KB becomes "digital graveyard." *Mitigation:* Assign one named curator per category; calendar quarterly audits.
- **Tool sprawl:** Adding Confluence/Notion on top of Google Drive + Slack + Jira creates *more* places to look. *Mitigation:* Designate the KB as the single source of truth for one content type (e.g., "decisions and runbooks live here, ephemeral chat stays in Slack").
- **Per-user cost compounds:** A 50-person project on Notion Business + AI runs ~$12k/year. *Mitigation:* Use Confluence if Atlassian-licensed already, or restrict editor seats and use free read-only access.
- **Migration lock-in:** Moving a 2,000-page Notion workspace to another tool = 10–20 hours of manual work (tech-insider.org, Mar 2026). *Mitigation:* Prefer Markdown-exportable tools; avoid heavy reliance on proprietary database features.

**Alternatives Considered:**
- **Obsidian + Git:** Excellent for individuals/small technical teams (free, local-first, Markdown). Excluded for most project teams because real-time collaboration is weak.
- **GitHub/GitLab Wiki:** Good for code-adjacent projects but poor for non-engineers.
- **SharePoint:** Default if Microsoft 365-licensed; included in M365 Business plans. Search is improving with Copilot but UX lags Notion/Confluence.
- **Google Drive folders:** Cheap but breaks past ~20 documents — no real search analytics, no versioning workflows.

### 5. Comparison

| Criteria | Weight | Notion | Confluence | Obsidian | SharePoint |
|----------|--------|--------|------------|----------|------------|
| Ease of use (non-technical) | High | 9/10 — block editor, intuitive | 7/10 — feels enterprise | 5/10 — Markdown learning curve | 6/10 — clunky but familiar |
| Cost at 20 seats/yr | High | 7/10 — ~$2.4k base, +$2.4k AI | 9/10 — ~$1.3k with Rovo AI | 10/10 — free (+$4/mo sync) | 9/10 — bundled in M365 |
| AI / agentic readiness | High | 8/10 — strong AI Q&A, $10/seat | 9/10 — Rovo AI bundled, 20+ agents | 5/10 — plugin-dependent | 8/10 — Copilot integration |
| Collaboration / real-time | High | 9/10 | 8/10 | 4/10 — local-first | 7/10 |
| Integration with dev tools | Medium | 7/10 | 10/10 — Jira native | 6/10 | 6/10 |
| Search quality | High | 8/10 | 8/10 | 7/10 (with plugins) | 7/10 (improving) |
| Export / lock-in risk | Medium | 6/10 — proprietary blocks | 7/10 | 10/10 — plain Markdown | 6/10 |
| **Weighted Score** | | **7.8/10** | **8.3/10** | **6.3/10** | **7.0/10** |

### 6. Recommendation

**Use Confluence if your team already has Atlassian/Jira; otherwise use Notion.** Confluence wins on weighted score primarily because Rovo AI is bundled (significant cost savings) and Jira integration is native — invaluable for project work where docs need to link to tickets. Notion wins for cross-functional teams that include non-technical members (marketing, ops, design) because the editor is meaningfully more pleasant and the template ecosystem (20,000+) accelerates setup.

Regardless of tool, invest *first* in:
1. A jobs-to-be-done category structure (5–8 top categories, max 3 levels)
2. An article template (title-as-question, intro, prerequisites, numbered steps, owner, last-reviewed date)
3. One named curator with quarterly review on the calendar

**When NOT to follow this recommendation:**
- **Solo / privacy-critical / offline work:** Choose Obsidian — free, local, Markdown-portable.
- **Heavy Microsoft 365 shop with Copilot licenses:** Stay in SharePoint + OneNote; avoid adding a third tool.
- **Customer-facing KB:** Notion/Confluence aren't suitable (no custom domains, no multi-tenant) — use Document360, Zendesk Guide, or Stonly instead.
- **Engineering-only project with docs-as-code culture:** GitHub Wiki or MkDocs/Docusaurus in the repo is better.

### 7. Next Steps

- **Week 1 — Define scope (owner: project lead):** Decide internal vs. external audience, pick 1–2 success metrics (e.g., "new hire onboarded in <5 days without 1:1," "RFC questions answered in KB rather than Slack").
- **Week 1 — Audit existing content (owner: project lead + 1 SME):** Pull existing Google Docs, Slack pinned items, meeting notes from last 90 days. Tag by topic to find the 10–20 highest-frequency questions.
- **Week 2 — Pick platform & set up IA (owner: project lead):** Run the comparison table above against your team's reality. Create 5–8 top-level categories organized by *task*, not by team.
- **Week 2–3 — Write 10–20 seed articles (owner: SMEs, reviewed by curator):** Use a consistent template. Get SME review before publishing.
- **Week 3 — Launch & promote (owner: project lead):** Announce in Slack/email, link from onboarding docs, pin in project channels. Treat launch as marketing.
- **Ongoing — Quarterly review cadence (owner: named curator):** Calendar invite, review every article, archive stale ones, draft articles for any recurring question that hit Slack instead of the KB.

**Items to verify independently:**
- **Current pricing** for Notion / Confluence / Obsidian Sync — vendors change tiers frequently; confirm at notion.so/pricing and atlassian.com/software/confluence/pricing before procurement.
- **Your existing license bundle:** Check if your org already has Confluence (via Jira) or SharePoint (via M365) — buying duplicates is the most common avoidable cost.
- **AI/RAG integration roadmap:** If you plan to layer an internal AI agent on the KB, verify the platform's API limits and embedding-export support — these vary and aren't always documented on pricing pages.
- **Data residency / compliance:** If working with regulated data (health, finance, EU PII), confirm the platform's data-residency options independently with the vendor's security team.