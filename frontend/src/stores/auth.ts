import { create } from "zustand"
import { api } from "../lib/api"

interface User {
  user_id: string
  username: string
  role: string
}

interface AuthState {
  token: string | null
  user: User | null
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  hydrate: () => void
}

// Restore token from localStorage on init
function loadFromStorage(): { token: string | null; user: User | null } {
  try {
    const token = localStorage.getItem("auth_token")
    const userStr = localStorage.getItem("auth_user")
    const user = userStr ? JSON.parse(userStr) : null
    return { token, user }
  } catch {
    return { token: null, user: null }
  }
}

const stored = loadFromStorage()
if (stored.token) {
  api.setToken(stored.token)
}

export const useAuthStore = create<AuthState>((set) => ({
  token: stored.token,
  user: stored.user,
  isAuthenticated: !!stored.token,

  login: async (username, password) => {
    const res = await api.post("/auth/login", { username, password })
    const { access_token, user } = res.data
    api.setToken(access_token)
    localStorage.setItem("auth_token", access_token)
    localStorage.setItem("auth_user", JSON.stringify(user))
    set({ token: access_token, user, isAuthenticated: true })
  },

  logout: () => {
    api.setToken(null)
    localStorage.removeItem("auth_token")
    localStorage.removeItem("auth_user")
    set({ token: null, user: null, isAuthenticated: false })
  },

  hydrate: () => {
    const { token, user } = loadFromStorage()
    if (token) {
      api.setToken(token)
      set({ token, user, isAuthenticated: true })
    }
  },
}))
