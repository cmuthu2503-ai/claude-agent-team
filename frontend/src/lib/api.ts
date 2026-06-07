// Base URL for backend API. In dev/prod the default `/api/v1` is served
// via Vite's proxy (see vite.config.ts) or the production reverse proxy.
// Override with VITE_API_BASE_URL when pointing at a non-proxied backend
// (e.g. local backend on http://localhost:8000 without the proxy).
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"

class ApiClient {
  private token: string | null = null

  setToken(token: string | null) {
    this.token = token
  }

  private headers(): HeadersInit {
    const h: HeadersInit = { "Content-Type": "application/json" }
    if (this.token) h["Authorization"] = `Bearer ${this.token}`
    return h
  }

  private authHeader(): HeadersInit {
    const h: HeadersInit = {}
    if (this.token) h["Authorization"] = `Bearer ${this.token}`
    return h
  }

  private handle401() {
    this.token = null
    localStorage.removeItem("auth_token")
    localStorage.removeItem("auth_user")
    window.location.href = "/login"
  }

  async get<T = any>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, { headers: this.headers() })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }

  async post<T = any>(path: string, body?: any): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }

  async postForm<T = any>(path: string, formData: FormData): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: this.authHeader(),
      body: formData,
    })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }

  async put<T = any>(path: string, body?: any): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PUT",
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }

  async patch<T = any>(path: string, body?: any): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PATCH",
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }

  async delete<T = any>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      headers: this.headers(),
    })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    // FastAPI DELETE routes commonly return 204 No Content with an empty
    // body — calling res.json() on that throws "unexpected end of data".
    // Treat any 204 / empty body as a successful no-op result.
    if (res.status === 204) return undefined as T
    const text = await res.text()
    return (text ? JSON.parse(text) : undefined) as T
  }
}

export const api = new ApiClient()
