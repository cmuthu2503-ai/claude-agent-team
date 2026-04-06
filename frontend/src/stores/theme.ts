import { create } from "zustand"

export type ThemeId = "linear" | "vercel" | "discord" | "flat" | "brutalist" | "y2k"
export type ThemeMode = "dark" | "light"

export interface ThemeInfo {
  id: ThemeId
  name: string
  description: string
  defaultMode: ThemeMode
}

export const THEMES: ThemeInfo[] = [
  { id: "linear", name: "Linear", description: "Purple accents, clean", defaultMode: "dark" },
  { id: "vercel", name: "Vercel", description: "Minimal, monospace", defaultMode: "dark" },
  { id: "discord", name: "Discord", description: "Blurple, channel-style", defaultMode: "dark" },
  { id: "flat", name: "Flat Design", description: "Bold colors, zero shadows", defaultMode: "light" },
  { id: "brutalist", name: "Brutalist", description: "Raw, monospace", defaultMode: "light" },
  { id: "y2k", name: "Y2K", description: "Neon glow, retro-future", defaultMode: "dark" },
]

interface ThemeState {
  theme: ThemeId
  mode: ThemeMode
  setTheme: (theme: ThemeId) => void
  setMode: (mode: ThemeMode) => void
  toggleMode: () => void
}

const storedTheme = localStorage.getItem("app_theme") as ThemeId | null
const storedMode = localStorage.getItem("app_mode") as ThemeMode | null

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: storedTheme && THEMES.some((t) => t.id === storedTheme) ? storedTheme : "linear",
  mode: storedMode === "light" || storedMode === "dark" ? storedMode : "dark",

  setTheme: (theme) => {
    localStorage.setItem("app_theme", theme)
    set({ theme })
  },

  setMode: (mode) => {
    localStorage.setItem("app_mode", mode)
    set({ mode })
  },

  toggleMode: () => {
    const newMode = get().mode === "dark" ? "light" : "dark"
    localStorage.setItem("app_mode", newMode)
    set({ mode: newMode })
  },
}))
