// Scaffolded by the Agent Team platform. PROJECT_* constants are
// substituted at scaffold time — kept at module scope so the literal
// `{{...}}` placeholders never appear in a JSX expression context.
const PROJECT_NAME = "{{PROJECT_NAME}}"
const FRONTEND_PORT = "{{FRONTEND_PORT}}"

export default function App() {
  return (
    <div className="scaffold">
      <h1>{PROJECT_NAME}</h1>
      <p className="tag">Scaffolded by Agent Team · frontend-only</p>

      <section className="card">
        <h2>Frontend</h2>
        <p>This page is served by Vite on port {FRONTEND_PORT}.</p>
      </section>

      <footer>
        <p>Edit <code>frontend/src/App.tsx</code> to change this page.</p>
      </footer>
    </div>
  )
}
