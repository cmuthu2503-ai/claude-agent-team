import { useState } from "react"
import { useAuthStore } from "../stores/auth"

export function LoginPage() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const login = useAuthStore((s) => s.login)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  // Already logged in — redirect
  if (isAuthenticated) {
    window.location.href = "/"
    return null
  }

  const handleClick = async () => {
    console.log("Button clicked!", { username, password: "***" })
    if (!username || !password) {
      setError("Please enter username and password")
      return
    }
    setError("")
    setLoading(true)
    try {
      await login(username, password)
      console.log("Login succeeded, redirecting...")
      window.location.href = "/"
    } catch (err: any) {
      console.error("Login error:", err)
      setError(err?.message || "Invalid credentials")
    } finally {
      setLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "var(--text-primary)",
    fontFamily: "var(--font)",
    padding: "8px 12px",
    fontSize: 14,
    outline: "none",
    boxSizing: "border-box",
  }

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-primary)",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 384,
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--shadow)",
          padding: 32,
        }}
      >
        <h1
          style={{
            textAlign: "center",
            fontSize: 24,
            fontWeight: 700,
            color: "var(--text-primary)",
            marginBottom: 24,
          }}
        >
          Agent Team
        </h1>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <label
              style={{
                display: "block",
                fontSize: 14,
                fontWeight: 500,
                color: "var(--text-secondary)",
                marginBottom: 4,
              }}
            >
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              style={inputStyle}
              placeholder="admin"
              autoFocus
            />
          </div>
          <div>
            <label
              style={{
                display: "block",
                fontSize: 14,
                fontWeight: 500,
                color: "var(--text-secondary)",
                marginBottom: 4,
              }}
            >
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={inputStyle}
              placeholder="admin123"
              onKeyDown={(e) => e.key === "Enter" && handleClick()}
            />
          </div>
          {error && (
            <p style={{ fontSize: 14, color: "var(--danger)" }}>{error}</p>
          )}
          <button
            type="button"
            onClick={handleClick}
            disabled={loading}
            style={{
              width: "100%",
              borderRadius: "var(--radius)",
              background: "var(--accent)",
              color: "#fff",
              padding: "8px 16px",
              fontSize: 14,
              fontWeight: 500,
              border: "none",
              cursor: loading ? "not-allowed" : "pointer",
              opacity: loading ? 0.5 : 1,
            }}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </div>
        <p
          style={{
            marginTop: 16,
            textAlign: "center",
            fontSize: 12,
            color: "var(--text-muted)",
          }}
        >
          Default: admin / admin123
        </p>
      </div>
    </div>
  )
}
