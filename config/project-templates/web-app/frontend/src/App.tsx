import { useEffect, useState } from "react"

// Scaffolded by the Agent Team platform. The two PROJECT_* constants
// below are substituted at scaffold time — they're declared at module
// scope so the literal `{{...}}` placeholders never appear in a JSX
// expression context (where they'd be parsed as invalid JS block
// syntax). Replace freely.
const PROJECT_NAME = "{{PROJECT_NAME}}"
const FRONTEND_PORT = "{{FRONTEND_PORT}}"

export default function App() {
  const [backendMessage, setBackendMessage] = useState<string>("…fetching backend…")
  const [error, setError] = useState<string>("")

  useEffect(() => {
    fetch("/api/")
      .then((r) => {
        if (!r.ok) throw new Error(`Backend responded ${r.status}`)
        return r.json()
      })
      .then((d) => setBackendMessage(d.message ?? JSON.stringify(d)))
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="scaffold">
      <h1>{PROJECT_NAME}</h1>
      <p className="tag">Scaffolded by Agent Team · ready to extend</p>

      <section className="card">
        <h2>Frontend</h2>
        <p>This page is served by Vite on port {FRONTEND_PORT}.</p>
      </section>

      <section className="card">
        <h2>Backend handshake</h2>
        {error ? (
          <p className="err">{error}</p>
        ) : (
          <p className="ok">{backendMessage}</p>
        )}
      </section>

      <footer>
        <p>Edit <code>frontend/src/App.tsx</code> to change this page.</p>
      </footer>
    </div>
  )
}
