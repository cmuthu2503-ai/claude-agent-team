# Slide 1: Title
Accessibility Test Agent Architecture
Subtitle: Automating font-scaling and WCAG compliance for LLM-generated mobile apps

---

# Slide 2: Agenda
- The Problem: Why LLM-generated screens fail accessibility
- The Opportunity: What research tells us about LLM + rule-based testing
- Three-Layer Agent Architecture
- Detect → Correct → Verify loop
- Component comparison and trade-offs
- Implementation roadmap (8 weeks)
- Costs, risks, and mitigations
- Next steps

Speaker notes: We'll walk through the specific challenge with font scaling in LLM-generated code, present a research-backed architecture, and close with a concrete 8-week implementation plan.

---

# Slide 3: The Problem
- LLMs generate code with hardcoded dimensions, fixed heights, and no font-scaling support
- WCAG 1.4.4 requires 200% text scaling without content loss (Level AA — legally required)
- Manual testing of 5+ scale levels × dozens of screens is unsustainable
- Font-scaling bugs only manifest at runtime — static analysis alone can't catch them

Speaker notes: The root cause is training-data bias. Most code examples on the internet don't demonstrate accessibility-first patterns, so LLMs default to fixed constraints. This means every screen the LLM generates is likely to break at larger font sizes — text truncates, elements overlap, content gets pushed off-screen.

---

# Slide 4: What Research Tells Us
- AccessGuru (ASSETS '25): LLM + rule engine achieves **84% violation reduction**
- Standalone LLM prompting caps at ~50% reduction
- LLM-generated corrections achieve 77% similarity to human developer fixes
- Three violation categories need different tools: Syntactic → rules, Layout → rules + visual, Semantic → LLM required

Speaker notes: The AccessGuru research is the foundation of our approach. They proved that the combination of Axe rule-based scanning and LLM semantic analysis dramatically outperforms either approach alone. Importantly, they also showed the LLM can generate usable code fixes — not just detect problems.

---

# Slide 5: Three-Layer Architecture Overview
- **L1 — Static Code Analysis:** LLM linter catches anti-patterns at PR time
- **L2 — Runtime Rule-Based Scan:** Appium + Axe DevTools Mobile at multiple font scales
- **L3 — Visual-Semantic AI Analysis:** Multimodal LLM analyzes screenshots for layout breakage

Speaker notes: Each layer catches what the others miss. L1 is fast and cheap — it catches code-level problems like using dp instead of sp. L2 uses established accessibility rules at runtime. L3 is where the real magic happens — a vision-capable LLM looks at actual screenshots and identifies truncated text, overlapping elements, and other visual issues that no rule engine can detect.

---

# Slide 6: The Agent Loop — Detect → Correct → Verify
- **Detect:** Axe scan + screenshots at 1.0×, 1.3×, 1.5×, 2.0× font scales
- **Analyze:** Multimodal LLM reviews screenshots + view hierarchy XML
- **Correct:** LLM generates code fixes with corrective re-prompting
- **Verify:** Re-run scan to confirm violation score decreased; reject regressions

Speaker notes: This isn't a one-shot analysis. The agent iterates. It detects violations, generates a fix, then re-runs the full scan to verify the fix actually worked and didn't introduce new problems. Only improvements get surfaced in PR comments. This corrective re-prompting technique is what drove AccessGuru's 84% improvement rate.

---

# Slide 7: Component Comparison
- Full Agent (L1+L2+L3) scores **7.8/10** weighted across six criteria
- L1 alone: 5.5/10 — good for code patterns, blind to runtime issues
- L2 alone: 5.8/10 — good rules but misses semantic and visual violations
- Font-scaling detection jumps from 3/10 (L1) to **9/10** (Full Agent)
- Trade-off: higher pipeline complexity and API costs

Speaker notes: The weighted comparison makes the case clearly. No single layer is sufficient. L1 is a great starting point — nearly free and catches 60% of issues. But font-scaling detection specifically requires the visual analysis layer. The full agent is more complex to operate, but it's the only option that actually solves the font-scaling problem comprehensively.

---

# Slide 8: Tooling Stack
- **Static:** LLM code review + Android Lint + SwiftLint + custom rules
- **Runtime:** Appium + Axe DevTools Mobile (AxeUiAutomator2 / AxeXCUITest)
- **Font scaling:** ADB commands (Android) + XCUITest launch args (iOS)
- **Visual diffing:** Percy or Applitools for baseline screenshot comparison
- **AI analysis:** GPT-4o or Claude for multimodal screenshot reasoning

Speaker notes: We're not building everything from scratch. Axe DevTools Mobile already has Appium CI/CD integration. Appium already supports programmatic font-scale changes. We're orchestrating existing tools and adding the LLM intelligence layer on top. The key engineering work is the orchestration and the prompt engineering for the multimodal analysis.

---

# Slide 9: Implementation Roadmap
- **Weeks 1–2:** L1 static code linter (immediate PR gate value)
- **Weeks 2–4:** L2 Appium + Axe runtime scans at multiple scales
- **Weeks 3–4:** Screenshot capture pipeline with visual diff integration
- **Weeks 4–7:** L3 multimodal LLM analyzer + correction loop
- **Weeks 6–8:** CI/CD gating, tuning, false-positive reduction

Speaker notes: The phased approach is deliberate. L1 ships in two weeks and immediately starts catching the most common anti-patterns in generated code. Each subsequent phase adds coverage. By week 8 we have the full Detect-Correct-Verify loop running in CI. The phases overlap because different team members own different layers.

---

# Slide 10: Costs, Risks & Mitigations
- **API cost:** ~$0.02/screenshot analysis → mitigate with change detection, only scan modified screens
- **False positives:** LLM may over-flag → human-in-the-loop initially, build training corpus
- **iOS complexity:** Dynamic Type requires XCUITest args → use real device cloud
- **Fix quality:** LLM corrections may break things → full test suite gate before auto-merge
- **Latency:** 10–20 min full pipeline → parallel execution + incremental testing

Speaker notes: None of these risks are blockers. The biggest practical concern is false positives in the early weeks. We recommend starting L2 and L3 as warning-only — not blocking merges — until the team has tuned the ignore-list and built confidence in the results. Budget-wise, for a 50-screen app at 4 scale levels, we're looking at roughly $4 per full run.

---

# Slide 11: When This Approach May Not Fit
- Fewer than 2 releases/month → manual QA may suffice
- Pre-built accessible design system → L1 alone may catch 80%+
- Budget under $500/month → start with L1 (free) + Android Accessibility Scanner (free)

Speaker notes: This is an important slide. We don't want to over-engineer. If the team already uses a design system with accessible components, the LLM just needs to use those components correctly — and L1 static analysis can verify that. The full three-layer agent is most valuable when screens are generated dynamically and change frequently.

---

# Slide 12: Next Steps
- **This week:** Confirm tech stack (React Native / Flutter / native) and define violation taxonomy
- **Week 1–2:** Platform engineer builds L1 static linter; QA begins Appium setup
- **Week 3:** First automated font-scaling scans running in CI
- **Decision needed:** Axe DevTools Mobile licensing, device cloud vs. local emulators
- **Decision needed:** LLM provider selection (GPT-4o vs. Claude) based on cost and accuracy testing

Speaker notes: We need two decisions to unblock the work — the Axe licensing question and LLM provider choice. Everything else can start immediately. The QA lead should begin building the violation taxonomy this week based on Deque's text-scaling documentation. Let's aim to have L1 blocking PRs within two weeks.
