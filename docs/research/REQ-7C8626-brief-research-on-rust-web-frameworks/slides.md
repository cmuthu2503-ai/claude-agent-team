# Slide 1: Rust Web Frameworks 2026
Subtitle: Mature Ecosystem, Diverse Choices

---

# Slide 2: Agenda
- Current Framework Landscape
- Key Players & Strengths
- Performance & Use Cases
- Selection Criteria
- Recommendations

---

# Slide 3: The Big Five
- **Axum** - Modern default choice
- **Actix Web** - Performance leader
- **Rocket** - Developer productivity
- **Warp** - Functional composition
- **Salvo** - Cutting-edge features

Speaker notes: The Rust web framework space has consolidated around five major players, each serving distinct needs rather than directly competing.

---

# Slide 4: Axum - The Modern Default
- Type-safe extractors
- Tower ecosystem integration
- v0.8.8 (January 2026)
- Best for: General APIs, new projects

Speaker notes: Axum has become the go-to choice for new projects due to its excellent balance of type safety, performance, and ergonomics.

---

# Slide 5: Actix Web - Performance Champion
- Actor model architecture
- Highest throughput benchmarks
- v4.12.1 (November 2025)
- Best for: High-traffic production systems

Speaker notes: Actix Web remains unmatched for scenarios requiring maximum performance and has proven itself in large-scale production environments.

---

# Slide 6: Framework Positioning
- **Rocket**: Rapid development & prototyping
- **Warp**: Functional programming approach
- **Salvo**: HTTP/3 & automatic TLS/ACME

Speaker notes: The remaining frameworks each fill specific niches - Rocket for speed of development, Warp for functional paradigms, and Salvo for modern protocol support.

---

# Slide 7: Selection Criteria
- Performance requirements
- Development speed needs
- Team expertise & preferences
- Protocol & deployment requirements
- Ecosystem compatibility

Speaker notes: Framework selection should be based on specific project needs rather than trying to find a universal "best" choice.

---

# Slide 8: Our Recommendation
- **Start with Axum** for most projects
- **Evaluate specific needs** for alternatives
- **Prototype early** to validate choice
- **Consider long-term maintenance**

Speaker notes: While Axum serves as an excellent default, teams should evaluate their specific requirements and potentially prototype with multiple frameworks before making final decisions.

---

# Slide 9: Key Takeaways
- Rust web frameworks have reached maturity
- Each framework serves distinct use cases
- Performance is excellent across all options
- Choose based on specific requirements

Speaker notes: The Rust web ecosystem is now mature and production-ready, with framework choice depending more on specific needs than fundamental quality differences.
