# Product Requirements Document (PRD)
# Atlas - AI-Powered Leadership Advisor for Technical Client Partners

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.1 |
| Created Date | 2026-01-22 |
| Last Updated | 2026-01-22 |
| Status | Draft |
| Product Owner | TBD |

---

## 1. Executive Summary

### 1.1 Product Vision

Atlas is an AI-powered desktop application that serves as a personal leadership advisor for newly promoted Technical Client Partners (TCPs) in the Software, Cloud Services, and Consulting industries. Unlike generic coaching tools, Atlas combines deep expertise in TCP-specific challenges with actionable guidance—helping leaders navigate complex stakeholder situations, build trusted advisor relationships, and develop strategic thinking capabilities through direct recommendations grounded in real-world experience.

### 1.2 Problem Statement

Newly promoted Technical Client Partners face significant challenges that existing solutions fail to address:

- **Isolation in Leadership Growth**: TCPs often lack access to experienced mentors who understand the unique challenges of leading without direct authority
- **No Structured Feedback on Client Interactions**: After important calls, TCPs have no way to get objective feedback on their advisory approach, strategic thinking, or communication effectiveness
- **Reactive Rather Than Proactive Development**: Without continuous guidance, TCPs learn through costly trial and error rather than building on proven frameworks
- **Context-Switching Overload**: Managing multiple clients and stakeholders requires disciplined approaches that TCPs must discover on their own
- **Technical-to-Business Translation Gap**: TCPs struggle to bridge technical depth with business acumen without experienced guidance

### 1.3 Target Users

**Primary Users: Technical Client Partners (0-18 months in role)**
- Recently promoted from technical roles (engineers, architects, consultants)
- Lead entirely through influence—no direct reports
- Navigate multiple stakeholder groups simultaneously
- Need to build trusted advisor relationships with clients and internal teams
- Work in Software, Cloud Services, or Consulting industries

**Secondary Users: TCP Managers/Mentors**
- May review anonymized insights to understand team development needs
- Could recommend Atlas to newly promoted team members

---

## 2. Goals

- **G1**: Enable TCPs to receive structured, actionable feedback on client calls within minutes of upload
- **G2**: Provide on-demand access to expert leadership advisory through conversational AI
- **G3**: Accelerate TCP development by providing frameworks and tactics for common challenges
- **G4**: Create a persistent knowledge base of conversations and insights for continuous learning
- **G5**: Generate visual mind maps that help TCPs synthesize and retain discussion insights
- **G6**: Reduce time-to-competency for new TCPs by providing immediate access to experienced guidance

---

## 3. Product Overview

### 3.1 Core Features

1. **Call Recording Analysis Module** - Upload and analyze client call recordings for comprehensive feedback
2. **Conversational Advisory Interface** - Real-time chat with Atlas for leadership guidance
3. **Conversation History & Memory** - Persistent storage of all interactions with cross-session context
4. **Mind Map Visualization** - Interactive visual representation of discussion topics and relationships
5. **Progress Dashboard** - Track development journey and recurring themes
6. **Theme & Appearance Customization** - Selectable design themes with light/dark mode variants

### 3.2 System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Atlas Desktop Application                         │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │  Call Analysis  │  │   Advisory      │  │   Mind Map      │         │
│  │     Module      │  │   Chat Module   │  │   Generator     │         │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘         │
│           │                    │                    │                   │
│           └────────────────────┼────────────────────┘                   │
│                                │                                        │
│                    ┌───────────┴───────────┐                           │
│                    │    Core Services      │                           │
│                    │  ┌─────────────────┐  │                           │
│                    │  │ Audio Processor │  │                           │
│                    │  │ LLM Orchestrator│  │                           │
│                    │  │ Session Manager │  │                           │
│                    │  └─────────────────┘  │                           │
│                    └───────────┬───────────┘                           │
│                                │                                        │
│           ┌────────────────────┼────────────────────┐                  │
│           │                    │                    │                   │
│  ┌────────┴────────┐  ┌───────┴────────┐  ┌───────┴────────┐          │
│  │  Local SQLite   │  │  Clerk Auth    │  │  Anthropic API │          │
│  │    Database     │  │    Service     │  │    (Claude)    │          │
│  └─────────────────┘  └────────────────┘  └────────────────┘          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Feature Requirements

### 4.1 Call Recording Analysis Module

#### 4.1.1 Overview

Users upload audio recordings of client calls to receive comprehensive AI-powered analysis. Atlas evaluates the call from the TCP perspective, providing structured feedback on advisory effectiveness, strategic thinking, communication quality, and actionable improvements.

#### 4.1.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| CALL-001 | Support audio file uploads in MP3, WAV, M4A, and WebM formats | High |
| CALL-002 | Support video file uploads in MP4 and MOV formats (extract audio) | Medium |
| CALL-003 | Display upload progress with file size and estimated processing time | High |
| CALL-004 | Transcribe audio using speech-to-text service with speaker diarization | High |
| CALL-005 | Allow users to identify which speaker is the TCP in the recording | High |
| CALL-006 | Generate star rating (1-5) for overall call handling effectiveness | High |
| CALL-007 | Provide detailed breakdown of rating across dimensions (rapport, clarity, strategic value, next steps) | High |
| CALL-008 | Generate "What Went Well" section highlighting effective moments with timestamps | High |
| CALL-009 | Generate "Strategic Improvements" section with specific recommendations | High |
| CALL-010 | Create executive call summary (3-5 bullet points) | High |
| CALL-011 | Suggest "Next Best Topics" for follow-up conversations | High |
| CALL-012 | Generate interactive mind map of discussion topics and relationships | High |
| CALL-013 | Allow users to export analysis report as PDF | Medium |
| CALL-014 | Store call analyses with searchable metadata (date, client, rating) | High |
| CALL-015 | Support calls up to 2 hours in duration | Medium |
| CALL-016 | Process calls within 5 minutes for recordings under 30 minutes | High |

#### 4.1.3 UI Components

**Call Upload Screen**
```
┌─────────────────────────────────────────────────────────────────┐
│  📞 Call Analysis                                    [History ▼]│
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                                                         │   │
│  │     ┌─────────┐                                        │   │
│  │     │  📁    │     Drop your call recording here      │   │
│  │     └─────────┘     or click to browse                 │   │
│  │                                                         │   │
│  │     Supported: MP3, WAV, M4A, WebM, MP4, MOV           │   │
│  │     Max duration: 2 hours                               │   │
│  │                                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Recent Analyses                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 📊 Acme Corp Strategy Call    ★★★★☆   Jan 20, 2026     │  │
│  │ 📊 TechStart Onboarding       ★★★★★   Jan 18, 2026     │  │
│  │ 📊 GlobalBank Security Review ★★★☆☆   Jan 15, 2026     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Analysis Results Screen**
```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back    Acme Corp Strategy Call - Analysis      [Export PDF] │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Overall Rating                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ★ ★ ★ ★ ☆  4.0/5.0  "Strong advisory presence"        │   │
│  │                                                         │   │
│  │  Rapport Building    ████████░░  4.0                   │   │
│  │  Strategic Value     ███████░░░  3.5                   │   │
│  │  Communication       █████████░  4.5                   │   │
│  │  Action Orientation  ████████░░  4.0                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┐     │
│  │  Summary    │ What Worked │  Improve    │  Mind Map   │     │
│  └─────────────┴─────────────┴─────────────┴─────────────┘     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  📋 Call Summary                                        │   │
│  │                                                         │   │
│  │  • Discussed Q2 cloud migration timeline concerns      │   │
│  │  • Client expressed budget constraints for Phase 2     │   │
│  │  • Agreed to phased approach with quick wins first     │   │
│  │  • Security review scheduled for next week             │   │
│  │                                                         │   │
│  │  🎯 Next Best Topics                                    │   │
│  │  1. ROI framework for Phase 2 business case            │   │
│  │  2. Quick wins implementation roadmap                  │   │
│  │  3. Stakeholder alignment for security review          │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Mind Map View**
```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back    Discussion Mind Map                    [Full Screen] │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                    ┌─────────────────┐                          │
│                    │  Cloud Migration │                         │
│                    │    Strategy      │                         │
│                    └────────┬────────┘                          │
│           ┌─────────────────┼─────────────────┐                │
│           │                 │                 │                 │
│    ┌──────┴──────┐   ┌─────┴─────┐   ┌──────┴──────┐          │
│    │   Timeline  │   │   Budget  │   │  Security   │          │
│    │   Concerns  │   │ Constraints│   │   Review    │          │
│    └──────┬──────┘   └─────┬─────┘   └─────────────┘          │
│           │                │                                    │
│    ┌──────┴──────┐   ┌─────┴─────┐                             │
│    │ Q2 Deadline │   │  Phase 2  │                             │
│    │   Pressure  │   │   Funding │                             │
│    └─────────────┘   └───────────┘                             │
│                                                                 │
│  [Zoom +]  [Zoom -]  [Reset]  [Export PNG]                     │
└─────────────────────────────────────────────────────────────────┘
```

---

### 4.2 Conversational Advisory Interface

#### 4.2.1 Overview

A chat-based interface where users can engage in real-time conversations with Atlas to receive leadership advice. Atlas maintains the persona of an experienced leadership advisor specializing in Technical Client Partner development, using the RAPID advisory model and other frameworks defined in the system prompt.

#### 4.2.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| CHAT-001 | Display chat interface with message history and input field | High |
| CHAT-002 | Send user messages to Claude API with Atlas system prompt | High |
| CHAT-003 | Stream AI responses in real-time with typing indicator | High |
| CHAT-004 | Support markdown formatting in messages (lists, bold, code blocks) | High |
| CHAT-005 | Allow users to start new conversation sessions | High |
| CHAT-006 | Persist all conversations to local database | High |
| CHAT-007 | Display conversation list with titles and timestamps | High |
| CHAT-008 | Auto-generate conversation titles from first user message | Medium |
| CHAT-009 | Allow users to rename conversations | Medium |
| CHAT-010 | Allow users to delete conversations | Medium |
| CHAT-011 | Search across all conversation history | Medium |
| CHAT-012 | Load previous conversation context when resuming sessions | High |
| CHAT-013 | Display Atlas persona introduction on first conversation | High |
| CHAT-014 | Support conversation export as markdown file | Low |
| CHAT-015 | Show token usage indicator for current session | Low |
| CHAT-016 | Implement conversation summarization for long sessions | Medium |

#### 4.2.3 UI Components

**Advisory Chat Screen**
```
┌─────────────────────────────────────────────────────────────────┐
│  💬 Atlas Advisory                                              │
├──────────────────┬──────────────────────────────────────────────┤
│                  │                                              │
│  Conversations   │  Stakeholder Influence Challenge             │
│  ┌────────────┐  │  ─────────────────────────────────────────── │
│  │ + New Chat │  │                                              │
│  └────────────┘  │  ┌─────────────────────────────────────────┐ │
│                  │  │ 🅰️ Atlas                                 │ │
│  Today           │  │                                         │ │
│  ○ Stakeholder   │  │ Based on what you've described, here's  │ │
│    Influence...  │  │ what I'm seeing: the core issue seems   │ │
│  ○ Difficult     │  │ to be a lack of relationship equity     │ │
│    Conversation  │  │ with the engineering lead...            │ │
│                  │  │                                         │ │
│  Yesterday       │  │ Here's what I recommend:                │ │
│  ○ Client Call   │  │                                         │ │
│    Prep          │  │ 1. **Schedule a 15-minute coffee chat** │ │
│  ○ Technical     │  │    with the engineering lead...         │ │
│    Credibility   │  │                                         │ │
│                  │  └─────────────────────────────────────────┘ │
│  Last Week       │                                              │
│  ○ First 90 Days │  ┌─────────────────────────────────────────┐ │
│    Planning      │  │ 👤 You                                  │ │
│                  │  │ That makes sense. But what if they      │ │
│  [Search 🔍]     │  │ decline the meeting?                    │ │
│                  │  └─────────────────────────────────────────┘ │
│                  │                                              │
│                  │  ┌─────────────────────────────────────────┐ │
│                  │  │ Type your message...              [Send]│ │
│                  │  └─────────────────────────────────────────┘ │
└──────────────────┴──────────────────────────────────────────────┘
```

---

### 4.3 Conversation History & Memory

#### 4.3.1 Overview

All interactions with Atlas—both advisory conversations and call analyses—are persisted locally with full context. Atlas can reference previous conversations, track recurring themes, and provide continuity across sessions.

#### 4.3.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| HIST-001 | Store all chat messages with timestamps and session IDs | High |
| HIST-002 | Store all call analyses with metadata and full reports | High |
| HIST-003 | Index conversation content for full-text search | Medium |
| HIST-004 | Track conversation topics and themes using AI categorization | Medium |
| HIST-005 | Generate session summaries for long conversations | Medium |
| HIST-006 | Enable cross-session context retrieval for Atlas responses | High |
| HIST-007 | Display timeline view of all interactions | Low |
| HIST-008 | Support data export in JSON format for backup | Medium |
| HIST-009 | Support data import from backup files | Medium |
| HIST-010 | Implement automatic local backup on application close | Medium |
| HIST-011 | Allow users to set retention policies (e.g., delete after 1 year) | Low |

---

### 4.4 Mind Map Visualization

#### 4.4.1 Overview

Interactive visual mind maps that represent the structure of discussions, showing topics, subtopics, and their relationships. Similar to NotebookLM's audio overview visualization, these maps help TCPs synthesize and retain insights from calls and advisory sessions.

#### 4.4.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| MIND-001 | Generate mind map from call transcript analysis | High |
| MIND-002 | Generate mind map from advisory conversation | Medium |
| MIND-003 | Display hierarchical topic structure with main themes and subtopics | High |
| MIND-004 | Support zoom in/out and pan navigation | High |
| MIND-005 | Allow users to expand/collapse topic branches | High |
| MIND-006 | Highlight key insights and action items in mind map | Medium |
| MIND-007 | Support drag-and-drop reorganization of nodes | Low |
| MIND-008 | Export mind map as PNG image | Medium |
| MIND-009 | Export mind map as interactive HTML | Low |
| MIND-010 | Color-code nodes by category (topic, action, concern, opportunity) | Medium |
| MIND-011 | Show timestamps/references linking to source content | Medium |

---

### 4.5 Progress Dashboard

#### 4.5.1 Overview

A dashboard providing TCPs with visibility into their development journey, including conversation history, recurring themes, improvement trends, and suggested focus areas.

#### 4.5.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| DASH-001 | Display total number of advisory sessions and call analyses | Medium |
| DASH-002 | Show average call rating trend over time (chart) | Medium |
| DASH-003 | Identify and display recurring themes from conversations | Medium |
| DASH-004 | Highlight areas of improvement based on call feedback | Medium |
| DASH-005 | Suggest focus areas based on conversation patterns | Low |
| DASH-006 | Display activity calendar showing engagement frequency | Low |
| DASH-007 | Show progress against First 90 Days milestones (if tracking) | Low |

---

### 4.6 Authentication & User Management

#### 4.6.1 Overview

User authentication powered by Clerk, providing secure sign-in and user profile management for the desktop application.

#### 4.6.2 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| AUTH-001 | Integrate Clerk authentication for user sign-in | High |
| AUTH-002 | Support email/password authentication | High |
| AUTH-003 | Support OAuth providers (Google, Microsoft) | High |
| AUTH-004 | Display user profile with name and email | High |
| AUTH-005 | Allow users to update profile information | Medium |
| AUTH-006 | Implement secure token storage for API access | High |
| AUTH-007 | Handle session expiration and re-authentication | High |
| AUTH-008 | Support sign-out with local data retention option | Medium |
| AUTH-009 | Display authentication errors with clear messages | High |

---

### 4.7 Theme & Appearance Customization

#### 4.7.1 Overview

Users can personalize the application's visual appearance by selecting from four distinct design themes, each available in both light and dark mode variants. This feature allows users to match their aesthetic preferences and reduce eye strain based on their working environment. The theme selector is accessible from the Dashboard header for quick access.

#### 4.7.2 Available Themes

**Theme 1: Art Deco + Glassmorphism**

A sophisticated fusion of 1920s Art Deco elegance with modern glassmorphism effects. Features bold geometric patterns, luxurious gold/brass accents, and frosted glass UI elements with subtle transparency and blur effects.

| Aspect | Light Mode | Dark Mode |
|--------|------------|-----------|
| Background | Cream/Ivory (#FAF7F2) with subtle geometric patterns | Deep Navy (#0D1B2A) with gold geometric accents |
| Glass Panels | White with 70% opacity, 20px blur | Dark gray (#1B2838) with 60% opacity, 20px blur |
| Primary Accent | Brass/Gold (#C9A227) | Bright Gold (#FFD700) |
| Secondary | Deep Teal (#1A535C) | Soft Teal (#4ECDC4) |
| Borders | Thin gold lines with geometric corners | Glowing gold lines with Art Deco motifs |
| Typography | Elegant serif for headings (Playfair Display), clean sans-serif for body | Same with increased letter-spacing |
| Cards | Frosted glass effect with subtle gold borders | Dark glass with luminous gold trim |
| Shadows | Soft, diffused with warm tint | Subtle glow effects |

**Theme 2: Neumorphism + Swiss/International Design**

A tactile, soft UI inspired by neumorphic design principles combined with the clarity and grid-based precision of Swiss International style. Features extruded/inset elements that appear to emerge from or sink into the background.

| Aspect | Light Mode | Dark Mode |
|--------|------------|-----------|
| Background | Soft Gray (#E0E5EC) | Charcoal (#2D3436) |
| Raised Elements | Light convex shadows (top-left light, bottom-right dark) | Subtle raised effect with dark gradients |
| Inset Elements | Concave shadows creating pressed appearance | Deep inset with soft inner shadows |
| Primary Accent | Swiss Red (#FF0000) for key actions | Vibrant Red (#FF3B30) |
| Typography | Helvetica Neue or Inter, strict grid alignment | Same with high contrast ratios |
| Grid System | Strict 8px baseline grid, mathematical proportions | Same grid, enhanced with subtle lines |
| Cards | Soft extruded appearance, no hard borders | Raised from surface with subtle gradients |
| Buttons | Pill-shaped, raised with soft shadows | Glowing edges on hover |
| Spacing | Generous whitespace, golden ratio proportions | Same precision spacing |

**Theme 3: Swiss/International + Flat Design**

A minimalist, content-focused design combining Swiss precision with modern flat design principles. Maximum clarity, zero ornamentation, bold typography hierarchy.

| Aspect | Light Mode | Dark Mode |
|--------|------------|-----------|
| Background | Pure White (#FFFFFF) | True Black (#000000) or Dark Gray (#121212) |
| Primary Accent | Bold Blue (#0066FF) | Electric Blue (#007AFF) |
| Secondary | Signal Red (#FF3B30) for warnings/actions | Coral Red (#FF6B6B) |
| Typography | Helvetica, Inter, or SF Pro - bold weight hierarchy | Same with WCAG AAA contrast |
| Cards | Flat, no shadows, subtle 1px borders | Flat with subtle elevation via background color |
| Buttons | Solid filled or outlined, no gradients or shadows | High contrast solid fills |
| Icons | Monoline, geometric, consistent stroke width | Same with slight glow on interactive |
| Dividers | Thin 1px lines, minimal use | Subtle gray lines |
| Color Blocks | Bold, solid color sections for visual hierarchy | Deep saturated color blocks |
| Spacing | Mathematical grid (4px/8px base), generous margins | Same precise spacing system |

**Theme 4: Art Deco + Glassmorphism OS Design**

A modern operating system-inspired glassmorphism design with Art Deco elegance. Features frosted glass panels, layered transparency, and sophisticated color accents reminiscent of macOS/Windows 11 aesthetics combined with Art Deco refinement.

| Aspect | Light Mode | Dark Mode |
|--------|------------|-----------|
| Background | Frosted Lavender (#f5f3ff) with soft gradients | Deep Purple-Navy (#0f0a1a) |
| Glass Panels | White with 65% opacity, 20px blur, subtle shadows | Dark purple (#2d2640) with 50% opacity, 24px blur |
| Primary Accent | Rose Gold (#9D5465) | Gold (#D4AF37) |
| Secondary | Deep Purple (#4A3B5C) | Teal (#2DD4BF) |
| Borders | Semi-transparent rose gold borders | Semi-transparent gold borders with glow |
| Typography | Clean sans-serif with elegant spacing | Same with enhanced luminosity |
| Cards | Frosted glass effect with subtle shadows | Translucent glass with ambient glow |
| Shadows | Soft 8px-32px with purple tint | Deep shadows with subtle color bleed |
| Buttons | Frosted with hover luminosity | Glass with golden glow on interaction |
| Spacing | 1rem base radius, generous padding | Same with increased border radius |

#### 4.7.3 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| THEME-001 | Provide theme selector in Dashboard header (palette icon) and Settings | High |
| THEME-002 | Support four design themes: Art Deco + Glassmorphism, Neumorphism + Swiss, Swiss + Flat, Art Deco + Glassmorphism OS | High |
| THEME-003 | Provide light and dark mode variants for each design theme (8 total combinations) | High |
| THEME-004 | Allow independent selection of design theme and color mode (light/dark) | High |
| THEME-005 | Persist user's theme and color mode preferences to local database | High |
| THEME-006 | Apply theme changes immediately without application restart | High |
| THEME-007 | Provide keyboard shortcut to toggle light/dark mode (Ctrl/Cmd + Shift + D) | Medium |
| THEME-008 | Support system preference detection for initial light/dark mode | Medium |
| THEME-009 | Option to sync color mode with OS system preference | Medium |
| THEME-010 | Display theme preview thumbnails in settings before selection | Medium |
| THEME-011 | Ensure all themes meet WCAG 2.1 AA contrast requirements | High |
| THEME-012 | Apply consistent theming across all application screens and components | High |
| THEME-013 | Animate theme transitions smoothly (300ms ease-in-out) | Low |
| THEME-014 | Remember last used theme on application startup | High |

#### 4.7.4 UI Components

**Theme Settings Panel**
```
┌─────────────────────────────────────────────────────────────────┐
│  ⚙️ Settings                                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Appearance                                                     │
│  ───────────────────────────────────────────────────────────── │
│                                                                 │
│  Design Theme                                                   │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │ ┌─────────────┐ │ │ ┌─────────────┐ │ │ ┌─────────────┐ │   │
│  │ │ ░░░▓▓▓░░░  │ │ │ │  ◯    ◯    │ │ │ │ ═══════════ │ │   │
│  │ │ ▓░░░░░░░▓  │ │ │ │ ┌──┐  ┌──┐ │ │ │ │ ███  ░░░░░ │ │   │
│  │ │ ░░▓▓▓▓░░░  │ │ │ │ └──┘  └──┘ │ │ │ │ ░░░  ───── │ │   │
│  │ └─────────────┘ │ │ └─────────────┘ │ │ └─────────────┘ │   │
│  │  Art Deco +     │ │  Neumorphism +  │ │  Swiss +        │   │
│  │  Glassmorphism  │ │  Swiss Design   │ │  Flat Design    │   │
│  │     [● Active]  │ │     [ ]         │ │     [ ]         │   │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘   │
│                                                                 │
│  Color Mode                                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ☀️ Light    ◉───────────○    🌙 Dark                    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  [ ] Sync with system preference                               │
│                                                                 │
│  Preview                                                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                                                         │   │
│  │    [Sample UI preview showing selected theme]           │   │
│  │                                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│                                          [Reset to Default]    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. User Stories

### 5.1 Call Analysis User Stories

| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-001 | TCP | Upload a call recording | I can get feedback on my performance |
| US-002 | TCP | See a star rating for my call | I can quickly assess how I did |
| US-003 | TCP | Read specific feedback on what went well | I can reinforce effective behaviors |
| US-004 | TCP | Receive strategic improvement suggestions | I can improve my approach next time |
| US-005 | TCP | Get a concise call summary | I can quickly recall key points |
| US-006 | TCP | See suggested next topics | I know what to discuss in follow-up |
| US-007 | TCP | View a mind map of the discussion | I can visualize and retain the conversation structure |
| US-008 | TCP | Export analysis as PDF | I can share or archive my feedback |
| US-009 | TCP | Search past call analyses | I can find relevant feedback quickly |

**Acceptance Criteria for US-001:**
- [ ] User can drag and drop audio/video files onto upload area
- [ ] User can click upload area to open file browser
- [ ] System validates file format before upload
- [ ] System shows upload progress indicator
- [ ] System displays error message for unsupported formats
- [ ] Upload completes within 30 seconds for files under 100MB

**Acceptance Criteria for US-002:**
- [ ] Star rating (1-5) displays prominently after analysis
- [ ] Rating includes brief descriptor (e.g., "Strong advisory presence")
- [ ] Breakdown shows scores across 4 dimensions
- [ ] Dimension scores use visual progress bars

**Acceptance Criteria for US-007:**
- [ ] Mind map displays within 10 seconds of analysis completion
- [ ] Main topic appears at center with branches for subtopics
- [ ] User can zoom and pan the mind map
- [ ] User can expand/collapse branches
- [ ] Verify in browser using dev-browser skill

---

### 5.2 Advisory Chat User Stories

| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-010 | TCP | Start a new conversation with Atlas | I can get advice on a specific challenge |
| US-011 | TCP | Receive direct recommendations from Atlas | I have actionable guidance to follow |
| US-012 | TCP | See Atlas reference previous conversations | I get contextual advice that builds on history |
| US-013 | TCP | View all my past conversations | I can revisit previous advice |
| US-014 | TCP | Search my conversation history | I can find specific guidance quickly |
| US-015 | TCP | Rename conversations | I can organize my history meaningfully |
| US-016 | TCP | Delete conversations | I can remove outdated or irrelevant chats |

**Acceptance Criteria for US-010:**
- [ ] "New Chat" button creates fresh conversation
- [ ] Atlas displays persona introduction on first message
- [ ] User can type and send messages
- [ ] Atlas response streams in real-time
- [ ] Conversation auto-saves after each message
- [ ] Verify in browser using dev-browser skill

**Acceptance Criteria for US-011:**
- [ ] Atlas uses RAPID advisory model structure
- [ ] Responses include specific recommendations (not just questions)
- [ ] Responses include rationale ("why" behind advice)
- [ ] Responses include concrete examples or scripts when relevant
- [ ] Responses end with clear next steps

---

### 5.3 Authentication User Stories

| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-017 | New user | Sign up with email | I can create an account |
| US-018 | User | Sign in with Google/Microsoft | I can access the app quickly |
| US-019 | User | Stay signed in across sessions | I don't have to re-authenticate each time |
| US-020 | User | Sign out securely | My data is protected on shared devices |

**Acceptance Criteria for US-017:**
- [ ] Sign-up form validates email format
- [ ] Password requirements are clearly displayed
- [ ] Clerk handles email verification
- [ ] User lands on main dashboard after successful sign-up
- [ ] Verify in browser using dev-browser skill

---

### 5.4 Theme & Appearance User Stories

| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-021 | TCP | Select from different design themes | The app matches my aesthetic preferences |
| US-022 | TCP | Switch between light and dark mode | I can reduce eye strain in different lighting conditions |
| US-023 | TCP | Preview themes before applying them | I can see what each option looks like before committing |
| US-024 | TCP | Have my theme preference remembered | I don't have to reconfigure on each launch |
| US-025 | TCP | Sync dark mode with my OS settings | The app matches my system-wide preference automatically |
| US-026 | TCP | Use a keyboard shortcut to toggle dark mode | I can quickly switch modes without navigating to settings |

**Acceptance Criteria for US-021:**
- [ ] Settings panel displays three theme options with visual previews
- [ ] Clicking a theme option selects it immediately
- [ ] Theme changes apply across all screens without restart
- [ ] Active theme shows clear visual indicator
- [ ] Verify in browser using dev-browser skill

**Acceptance Criteria for US-022:**
- [ ] Light/Dark toggle is clearly visible in settings
- [ ] Toggle can be switched with single click/tap
- [ ] Mode change animates smoothly (300ms transition)
- [ ] All UI elements update to selected mode
- [ ] Contrast ratios meet WCAG AA standards in both modes
- [ ] Verify in browser using dev-browser skill

**Acceptance Criteria for US-023:**
- [ ] Theme selector shows thumbnail previews of each theme
- [ ] Hovering/selecting shows larger preview panel
- [ ] Preview accurately represents actual theme appearance
- [ ] Both light and dark variants can be previewed
- [ ] Verify in browser using dev-browser skill

**Acceptance Criteria for US-025:**
- [ ] Checkbox option "Sync with system preference" is available
- [ ] When enabled, app detects and follows OS dark/light mode
- [ ] App responds to OS preference changes in real-time
- [ ] Manual override is still possible when sync is disabled
- [ ] Verify in browser using dev-browser skill

---

## 6. Functional Requirements

- **FR-001**: The system must transcribe uploaded audio using a speech-to-text service with at least 95% accuracy for clear English speech
- **FR-002**: The system must identify different speakers in a recording (speaker diarization) and allow the user to designate which speaker is the TCP
- **FR-003**: The system must analyze call transcripts using Claude API with the Atlas system prompt to generate structured feedback
- **FR-004**: The system must generate a numerical rating (1-5, with 0.5 increments) based on defined evaluation criteria
- **FR-005**: The system must extract and structure "What Went Well" observations with specific examples from the transcript
- **FR-006**: The system must generate "Strategic Improvements" recommendations aligned with the RAPID advisory model
- **FR-007**: The system must create call summaries with 3-5 bullet points capturing key discussion topics and outcomes
- **FR-008**: The system must suggest 2-4 "Next Best Topics" based on conversation context and TCP development needs
- **FR-009**: The system must generate mind map data structures representing topic hierarchies and relationships
- **FR-010**: The system must render interactive mind maps with zoom, pan, and expand/collapse capabilities
- **FR-011**: The system must maintain conversation context across messages within a session
- **FR-012**: The system must retrieve relevant context from previous sessions when generating responses
- **FR-013**: The system must stream AI responses in real-time as they are generated
- **FR-014**: The system must persist all data to local SQLite database with encryption
- **FR-015**: The system must support full-text search across conversations and call analyses
- **FR-016**: The system must integrate with Clerk for authentication and user management
- **FR-017**: The system must securely store API keys and authentication tokens
- **FR-018**: The system must handle API rate limits gracefully with user feedback
- **FR-019**: The system must provide three selectable design themes: Art Deco + Glassmorphism, Neumorphism + Swiss, and Swiss + Flat
- **FR-020**: The system must provide light and dark mode variants for each design theme (6 total visual configurations)
- **FR-021**: The system must persist user theme and color mode preferences in the local database
- **FR-022**: The system must apply theme changes immediately without requiring application restart
- **FR-023**: The system must detect and optionally sync with the operating system's light/dark mode preference
- **FR-024**: The system must ensure all theme variants meet WCAG 2.1 Level AA color contrast requirements
- **FR-025**: The system must provide smooth animated transitions (300ms) when switching themes or color modes

---

## 7. Non-Functional Requirements

### 7.1 Performance

| Requirement | Target |
|-------------|--------|
| Application startup time | < 3 seconds |
| Chat message response initiation | < 1 second |
| Call analysis processing (30 min recording) | < 5 minutes |
| Mind map rendering | < 2 seconds |
| Search results display | < 500ms |
| Database query response | < 100ms |

### 7.2 Scalability

| Requirement | Target |
|-------------|--------|
| Concurrent local sessions | 1 (single-user desktop app) |
| Stored conversations | Unlimited (disk space dependent) |
| Stored call analyses | Unlimited (disk space dependent) |
| Maximum call recording duration | 2 hours |
| Maximum file upload size | 500 MB |

### 7.3 Security

| Requirement | Description |
|-------------|-------------|
| Authentication | Clerk-managed authentication with OAuth support |
| Data encryption at rest | SQLite database encrypted with user-derived key |
| API key storage | Secure credential storage using OS keychain |
| Data transmission | All API calls over HTTPS/TLS 1.3 |
| Session management | Secure token refresh with Clerk |
| Local data access | Protected by OS-level user authentication |

### 7.4 Accessibility

| Requirement | Standard |
|-------------|----------|
| Keyboard navigation | Full support for all features |
| Screen reader support | ARIA labels on all interactive elements |
| Color contrast | WCAG 2.1 Level AA compliance |
| Text scaling | Support 100%-200% zoom |
| Focus indicators | Visible focus states on all interactive elements |

### 7.5 Reliability

| Requirement | Target |
|-------------|--------|
| Application crash rate | < 0.1% of sessions |
| Data loss prevention | Auto-save every 30 seconds |
| Offline capability | View history and cached content |
| Error recovery | Graceful degradation with user notification |

---

## 8. Non-Goals (Out of Scope for v1.0)

| Feature | Reason |
|---------|--------|
| Real-time call analysis | Requires complex audio streaming; batch analysis first |
| Multi-user collaboration | Single-user desktop app; team features in future |
| Mobile application | Desktop-first to establish core functionality |
| Calendar integration | Focus on core advisory features first |
| CRM integration | Future enhancement based on user demand |
| Custom AI model fine-tuning | Use prompt engineering with Claude initially |
| Voice input for chat | Text-based chat is sufficient for v1.0 |
| Video playback with transcript sync | Audio-only analysis for initial release |
| Automated call recording | Users upload existing recordings |
| Multi-language support | English only for v1.0 |

---

## 9. Design Considerations

### 9.1 Visual Design Principles

- **Professional & Trustworthy**: Clean interface that conveys expertise and reliability
- **Focused & Distraction-Free**: Minimize chrome; content takes center stage
- **Personalized Experience**: Multiple theme options allow users to match their preferences
- **Information Hierarchy**: Clear visual hierarchy guiding user attention
- **Accessibility First**: All themes must meet WCAG 2.1 AA standards

### 9.2 Theme System Architecture

Atlas offers three distinct design themes, each with light and dark mode variants, giving users six visual configurations to choose from.

#### 9.2.1 Theme 1: Art Deco + Glassmorphism

**Design Philosophy**: Merges the geometric elegance and luxury of 1920s Art Deco with contemporary glassmorphism effects. Creates a sophisticated, premium feel that stands out from typical productivity apps.

**Key Characteristics**:
- Frosted glass panels with backdrop blur effects
- Bold geometric patterns and decorative borders
- Gold/brass accent colors conveying luxury
- Elegant serif typography for headings
- Layered transparency creating depth

**Color System**:

| Token | Light Mode | Dark Mode |
|-------|------------|-----------|
| `--bg-primary` | #FAF7F2 (Cream) | #0D1B2A (Deep Navy) |
| `--bg-glass` | rgba(255,255,255,0.7) | rgba(27,40,56,0.6) |
| `--accent-primary` | #C9A227 (Brass) | #FFD700 (Gold) |
| `--accent-secondary` | #1A535C (Deep Teal) | #4ECDC4 (Soft Teal) |
| `--text-primary` | #1A1A1A | #F5F5F5 |
| `--text-secondary` | #4A4A4A | #B0B0B0 |
| `--border` | rgba(201,162,39,0.3) | rgba(255,215,0,0.4) |
| `--success` | #2D6A4F | #40916C |
| `--warning` | #BC6C25 | #DDA15E |
| `--error` | #9B2226 | #E63946 |

**Typography**:
- Headings: Playfair Display (serif)
- Body: Inter or Lato (sans-serif)
- Monospace: JetBrains Mono

**Effects**:
- `backdrop-filter: blur(20px)` on glass elements
- Subtle gold drop shadows
- Geometric corner decorations on cards

---

#### 9.2.2 Theme 2: Neumorphism + Swiss/International Design

**Design Philosophy**: Combines the tactile, soft UI of neumorphism with the grid-based precision and clarity of Swiss International design. Creates a modern, approachable interface that feels tangible yet organized.

**Key Characteristics**:
- Soft extruded/inset elements with dual shadows
- Strict mathematical grid system (8px baseline)
- Minimal color palette with Swiss Red accents
- Clean sans-serif typography (Helvetica/Inter)
- Generous whitespace and precise alignment

**Color System**:

| Token | Light Mode | Dark Mode |
|-------|------------|-----------|
| `--bg-primary` | #E0E5EC (Soft Gray) | #2D3436 (Charcoal) |
| `--bg-raised` | #E0E5EC | #3D4447 |
| `--shadow-light` | #FFFFFF | #3D4447 |
| `--shadow-dark` | #A3B1C6 | #1A1D1E |
| `--accent-primary` | #FF0000 (Swiss Red) | #FF3B30 |
| `--accent-secondary` | #0066CC | #4DA3FF |
| `--text-primary` | #2D3436 | #E8E8E8 |
| `--text-secondary` | #636E72 | #A0A0A0 |
| `--success` | #00A86B | #00D68F |
| `--warning` | #FF9500 | #FFCC00 |
| `--error` | #FF3B30 | #FF6B6B |

**Typography**:
- Headings: Helvetica Neue Bold or Inter Bold
- Body: Helvetica Neue or Inter Regular
- Monospace: SF Mono or JetBrains Mono

**Effects**:
- Raised: `box-shadow: 8px 8px 16px var(--shadow-dark), -8px -8px 16px var(--shadow-light)`
- Inset: `box-shadow: inset 4px 4px 8px var(--shadow-dark), inset -4px -4px 8px var(--shadow-light)`
- No hard borders; depth conveyed through shadows only

---

#### 9.2.3 Theme 3: Swiss/International + Flat Design

**Design Philosophy**: The purest expression of form-follows-function, combining Swiss precision with modern flat design. Maximum clarity, zero ornamentation, bold typography hierarchy. Content is king.

**Key Characteristics**:
- Completely flat UI with no shadows or gradients
- Bold, solid colors for visual hierarchy
- Strong typographic scale and weight contrast
- Precise 4px/8px grid system
- Minimal borders, color blocks define areas

**Color System**:

| Token | Light Mode | Dark Mode |
|-------|------------|-----------|
| `--bg-primary` | #FFFFFF (Pure White) | #121212 (Near Black) |
| `--bg-secondary` | #F5F5F5 | #1E1E1E |
| `--bg-tertiary` | #E8E8E8 | #2C2C2C |
| `--accent-primary` | #0066FF (Bold Blue) | #007AFF |
| `--accent-secondary` | #FF3B30 (Signal Red) | #FF6B6B |
| `--text-primary` | #000000 | #FFFFFF |
| `--text-secondary` | #666666 | #999999 |
| `--border` | #E0E0E0 | #333333 |
| `--success` | #34C759 | #30D158 |
| `--warning` | #FF9500 | #FFD60A |
| `--error` | #FF3B30 | #FF453A |

**Typography**:
- Headings: Inter Black / Helvetica Neue Bold
- Body: Inter Regular / Helvetica Neue
- Monospace: SF Mono or IBM Plex Mono

**Effects**:
- No shadows (zero `box-shadow`)
- No gradients (solid `background-color` only)
- Borders: 1px solid when needed
- Color blocks for section differentiation

---

### 9.3 Theme Implementation Guidelines

**CSS Custom Properties**: All themes should be implemented using CSS custom properties (variables) to enable runtime switching without page reload.

```css
/* Example theme structure */
[data-theme="art-deco"][data-mode="light"] {
  --bg-primary: #FAF7F2;
  --accent-primary: #C9A227;
  /* ... */
}

[data-theme="art-deco"][data-mode="dark"] {
  --bg-primary: #0D1B2A;
  --accent-primary: #FFD700;
  /* ... */
}
```

**Theme Switching Logic**:
1. Store theme preference in `users.preferences_json`
2. Apply theme on app initialization before first paint
3. Listen for OS preference changes via `prefers-color-scheme` media query
4. Animate transitions using `transition: background-color 300ms ease, color 300ms ease`

**Default Theme**: Swiss + Flat (Light Mode) - cleanest, most universally accessible starting point.

### 9.4 Typography System

Each theme uses a consistent type scale regardless of font family:

| Level | Size | Weight | Line Height | Use Case |
|-------|------|--------|-------------|----------|
| Display | 32px | Bold | 1.2 | Page titles |
| H1 | 24px | Bold | 1.3 | Section headers |
| H2 | 20px | Semibold | 1.4 | Subsection headers |
| H3 | 16px | Semibold | 1.4 | Card titles |
| Body | 14px | Regular | 1.5 | Main content |
| Small | 12px | Regular | 1.4 | Captions, metadata |
| Mono | 13px | Regular | 1.5 | Code, technical data |

### 9.5 Key UI Patterns

- **Chat Interface**: Familiar messaging pattern with clear sender distinction
- **Card-Based Layout**: Analysis results in scannable cards
- **Progressive Disclosure**: Show summary first, details on demand
- **Contextual Actions**: Actions appear where relevant, not in distant menus
- **Consistent Spacing**: 8px base unit, multiples for larger spaces (16px, 24px, 32px, 48px)

---

## 10. Technical Considerations

### 10.1 Suggested Technology Stack

| Component | Recommendation | Rationale |
|-----------|----------------|-----------|
| Desktop Framework | Electron or Tauri | Cross-platform desktop with web technologies |
| Frontend | React + TypeScript | Component-based, type-safe, large ecosystem |
| UI Components | Tailwind CSS + Radix UI | Utility-first styling with accessible primitives |
| State Management | Zustand | Simple, performant state management |
| Local Database | SQLite (via better-sqlite3 or sql.js) | Reliable local storage, no server required |
| AI Provider | Anthropic Claude API | Aligned with product requirements |
| Speech-to-Text | AssemblyAI or Whisper | High accuracy, speaker diarization |
| Mind Map Rendering | D3.js or React Flow | Flexible visualization library |
| Authentication | Clerk | Managed auth with OAuth support |
| Build Tool | Vite | Fast development and builds |

### 10.2 Database Design

#### 10.2.1 Entity Relationship Diagram

```
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│      User        │       │   Conversation   │       │     Message      │
├──────────────────┤       ├──────────────────┤       ├──────────────────┤
│ PK user_id       │──┐    │ PK conversation_id│──┐   │ PK message_id    │
│    clerk_id      │  │    │ FK user_id       │  │   │ FK conversation_id│
│    email         │  └───>│    title         │  └──>│    role          │
│    name          │       │    created_at    │      │    content       │
│    theme         │       │    updated_at    │      │    created_at    │
│    color_mode    │       │    summary       │      └──────────────────┘
│    created_at    │       └──────────────────┘
└──────────────────┘
                                   │
                                   │
┌──────────────────┐       ┌───────┴──────────┐       ┌──────────────────┐
│   CallAnalysis   │       │  ConversationTag │       │       Tag        │
├──────────────────┤       ├──────────────────┤       ├──────────────────┤
│ PK analysis_id   │       │ FK conversation_id│<─────│ PK tag_id        │
│ FK user_id       │       │ FK tag_id        │      │    name          │
│    file_name     │       └──────────────────┘      │    category      │
│    duration_sec  │                                  └──────────────────┘
│    transcript    │
│    rating        │       ┌──────────────────┐
│    rating_details│       │    MindMap       │
│    summary       │       ├──────────────────┤
│    went_well     │──────>│ PK mindmap_id    │
│    improvements  │       │ FK analysis_id   │
│    next_topics   │       │ FK conversation_id│
│    mindmap_data  │       │    nodes_json    │
│    created_at    │       │    created_at    │
└──────────────────┘       └──────────────────┘
```

#### 10.2.2 Table Definitions

##### Users Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| user_id | TEXT | PK, NOT NULL | UUID primary identifier |
| clerk_id | TEXT | UNIQUE, NOT NULL | Clerk user identifier |
| email | TEXT | NOT NULL | User email address |
| name | TEXT | | User display name |
| theme | TEXT | NOT NULL, DEFAULT 'neumorphism-swiss' | Selected design theme: 'art-deco-glass', 'neumorphism-swiss', 'swiss-flat', 'art-deco-glass-os' |
| color_mode | TEXT | NOT NULL, DEFAULT 'light' | Color mode: 'light', 'dark', 'system' |
| preferences_json | TEXT | | JSON blob for additional user settings |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |
| updated_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Indexes:**
- `idx_users_clerk_id` on `clerk_id` - Lookup by Clerk ID

**Theme Values:**
- `art-deco-glass` - Art Deco + Glassmorphism theme
- `neumorphism-swiss` - Neumorphism + Swiss/International theme (default)
- `swiss-flat` - Swiss/International + Flat Design theme
- `art-deco-glass-os` - Art Deco + Glassmorphism OS Design theme

##### Conversations Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| conversation_id | TEXT | PK, NOT NULL | UUID primary identifier |
| user_id | TEXT | FK, NOT NULL | Reference to users table |
| title | TEXT | NOT NULL | Conversation title (auto or manual) |
| summary | TEXT | | AI-generated session summary |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |
| updated_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Indexes:**
- `idx_conversations_user_id` on `user_id` - User's conversations
- `idx_conversations_updated_at` on `updated_at` - Sort by recent

**Relationships:**
- `user_id` references `users(user_id)` ON DELETE CASCADE

##### Messages Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| message_id | TEXT | PK, NOT NULL | UUID primary identifier |
| conversation_id | TEXT | FK, NOT NULL | Reference to conversations |
| role | TEXT | NOT NULL | 'user' or 'assistant' |
| content | TEXT | NOT NULL | Message text content |
| tokens_used | INTEGER | | Token count for this message |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Indexes:**
- `idx_messages_conversation_id` on `conversation_id` - Messages in conversation
- `idx_messages_content_fts` - Full-text search index on content

**Relationships:**
- `conversation_id` references `conversations(conversation_id)` ON DELETE CASCADE

##### CallAnalyses Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| analysis_id | TEXT | PK, NOT NULL | UUID primary identifier |
| user_id | TEXT | FK, NOT NULL | Reference to users table |
| file_name | TEXT | NOT NULL | Original uploaded file name |
| file_path | TEXT | | Local path to stored audio |
| duration_seconds | INTEGER | NOT NULL | Recording duration |
| transcript | TEXT | NOT NULL | Full transcription text |
| speaker_map_json | TEXT | | JSON mapping speakers to roles |
| rating | REAL | NOT NULL | Overall rating (1.0-5.0) |
| rating_details_json | TEXT | NOT NULL | JSON with dimension breakdowns |
| summary_json | TEXT | NOT NULL | JSON array of summary bullets |
| went_well_json | TEXT | NOT NULL | JSON array of positive observations |
| improvements_json | TEXT | NOT NULL | JSON array of recommendations |
| next_topics_json | TEXT | NOT NULL | JSON array of suggested topics |
| mindmap_data_json | TEXT | NOT NULL | JSON structure for mind map |
| client_name | TEXT | | Optional client identifier |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Indexes:**
- `idx_call_analyses_user_id` on `user_id` - User's analyses
- `idx_call_analyses_created_at` on `created_at` - Sort by date
- `idx_call_analyses_rating` on `rating` - Filter by rating
- `idx_call_analyses_transcript_fts` - Full-text search on transcript

**Relationships:**
- `user_id` references `users(user_id)` ON DELETE CASCADE

##### Tags Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| tag_id | TEXT | PK, NOT NULL | UUID primary identifier |
| name | TEXT | UNIQUE, NOT NULL | Tag name |
| category | TEXT | | Category (topic, skill, stakeholder) |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Indexes:**
- `idx_tags_name` on `name` - Lookup by tag name
- `idx_tags_category` on `category` - Filter by category

##### ConversationTags Junction Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| conversation_id | TEXT | FK, NOT NULL | Reference to conversations |
| tag_id | TEXT | FK, NOT NULL | Reference to tags |
| created_at | TEXT | NOT NULL | ISO 8601 timestamp |

**Constraints:**
- PRIMARY KEY (conversation_id, tag_id)

**Relationships:**
- `conversation_id` references `conversations(conversation_id)` ON DELETE CASCADE
- `tag_id` references `tags(tag_id)` ON DELETE CASCADE

#### 10.2.3 Data Model Considerations

- **Normalization**: Database follows 3NF with JSON columns for flexible structured data (preferences, analysis details) that don't require relational queries
- **Soft Delete Strategy**: Not implemented in v1.0; hard deletes cascade through relationships. Future versions may add `deleted_at` columns
- **Audit Trail**: `created_at` and `updated_at` timestamps on all tables; consider adding audit log table in future
- **Multi-tenancy**: Single-user desktop app; `user_id` foreign keys support potential future multi-user scenarios
- **Full-Text Search**: SQLite FTS5 extension for conversation and transcript search
- **JSON Storage**: Complex nested data (ratings breakdown, mind map nodes) stored as JSON for flexibility

### 10.3 Integration Points

1. **Anthropic Claude API** - Primary AI provider for advisory responses and call analysis
2. **Speech-to-Text Service** - AssemblyAI or OpenAI Whisper for transcription
3. **Clerk Authentication** - User authentication and session management
4. **OS Keychain** - Secure credential storage (Keytar or native APIs)

### 10.4 Known Constraints and Dependencies

- **API Costs**: Claude API and speech-to-text services have per-token/per-minute pricing
- **Internet Required**: AI features require internet connectivity (offline mode limited to viewing history)
- **Local Storage**: All data stored locally; no cloud sync in v1.0
- **Audio Processing**: Large files may require significant processing time and memory
- **Rate Limits**: Must handle Anthropic API rate limits gracefully

---

## 11. Future Enhancements (Out of Scope for v1.0)

| Feature | Description |
|---------|-------------|
| Cloud Sync | Sync conversation history across devices |
| Team Dashboard | Manager view of anonymized team development patterns |
| Real-Time Call Analysis | Live feedback during calls |
| Role-Play Practice | Simulated conversations for skill building |
| Integration APIs | Connect with Zoom, Teams, Salesforce |
| Custom Frameworks | User-defined evaluation criteria and frameworks |
| Progress Certifications | Formal milestones and achievements |
| Community Features | Anonymous peer learning and shared insights |
| Mobile Companion App | Quick access to advice on-the-go |
| Voice-to-Voice Advisory | Speak directly with Atlas |

---

## 12. Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Weekly Active Users | 80% of registered users | Application analytics |
| Call Analyses per User per Month | ≥ 4 | Database queries |
| Advisory Sessions per User per Month | ≥ 8 | Database queries |
| Average Call Rating Improvement | +0.5 stars over 90 days | Rating trend analysis |
| Session Duration | 10-20 minutes average | Application analytics |
| Feature Adoption (Mind Maps) | 60% of users use within first month | Feature usage tracking |
| User Retention (30-day) | ≥ 70% | Cohort analysis |
| NPS Score | ≥ 50 | In-app survey |

---

## 13. Appendix

### 13.1 Glossary

| Term | Definition |
|------|------------|
| TCP | Technical Client Partner - A client-facing technical role that leads through influence without direct reports |
| RAPID Model | Atlas's advisory framework: Receive, Analyze, Prescribe, Illustrate, Define next steps |
| Trust Equation | Framework: Trustworthiness = (Credibility + Reliability + Intimacy) / Self-Orientation |
| Speaker Diarization | The process of identifying and separating different speakers in an audio recording |
| Mind Map | Visual diagram representing topics and their hierarchical relationships |
| Session | A single conversation thread with Atlas |
| Analysis | The structured feedback generated from a call recording |
| First 90 Days | The critical onboarding period for new TCPs, with phased milestones |
| Art Deco | A visual arts design style from the 1920s-30s characterized by bold geometric forms, rich colors, and lavish ornamentation |
| Glassmorphism | A UI design trend featuring frosted-glass effects achieved through background blur and transparency |
| Neumorphism | A UI design approach creating soft, extruded shapes using subtle shadows to simulate physical depth |
| Swiss/International Design | A graphic design style emphasizing cleanliness, readability, and objectivity through grid-based layouts and sans-serif typography |
| Flat Design | A minimalist UI design approach using simple 2D elements without shadows, gradients, or textures |
| Color Mode | The light or dark variant of a theme that affects overall brightness and color contrast |
| WCAG | Web Content Accessibility Guidelines - standards ensuring web content is accessible to people with disabilities |

### 13.2 Atlas Persona Reference

Atlas is an expert leadership advisor with the following characteristics:
- **Voice**: Authoritative yet approachable, direct yet empathetic, experienced yet humble
- **Approach**: Provides direct recommendations (not just questions), explains rationale, gives concrete examples
- **Focus Areas**: Trusted Advisor development, Bridge Building between technical and business, Strategic Orchestration without formal authority
- **Frameworks Used**: RAPID Advisory Model, Trust Equation, Stakeholder Influence Playbook, Executive Communication Framework, SBI+R for difficult conversations

For full persona specification, see `Instruction.txt`.

### 13.3 Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-22 | Atlas PRD Generator | Initial draft |
| 1.1 | 2026-01-22 | Atlas PRD Generator | Added Theme & Appearance Customization feature with three design themes (Art Deco + Glassmorphism, Neumorphism + Swiss, Swiss + Flat) and light/dark mode support |
| 1.2 | 2026-01-23 | Atlas PRD Generator | Added fourth theme "Art Deco + Glassmorphism OS Design"; moved theme selector from Settings to Dashboard header; updated theme count from 3 to 4 (8 total variants with light/dark modes) |

---

*End of Document*
