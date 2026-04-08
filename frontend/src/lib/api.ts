const API_BASE = "/api/v1"

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

  async delete<T = any>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      headers: this.headers(),
    })
    if (res.status === 401) { this.handle401(); throw new Error("Session expired") }
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.json()
  }
}

export const api = new ApiClient()
