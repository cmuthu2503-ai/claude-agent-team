import { create } from "zustand"

export type ThemeId = "linear" | "vercel" | "discord" | "flat" | "brutalist" | "y2k"
export type ThemeMode = "dark" | "light"

export interface ThemeInfo {
  id: ThemeId
  name: string
  description: string
  mode: ThemeMode
}

export const THEMES: ThemeInfo[] = [
  { id: "linear", name: "Linear", description: "Dark, purple accents, clean", mode: "dark" },
  { id: "vercel", name: "Vercel", description: "Black & white, ultra-minimal", mode: "dark" },
  { id: "discord", name: "Discord", description: "Dark, blurple, channel-style", mode: "dark" },
  { id: "flat", name: "Flat Design", description: "Bold colors, zero shadows", mode: "light" },
  { id: "brutalist", name: "Brutalist", description: "Raw, monospace, anti-design", mode: "light" },
  { id: "y2k", name: "Y2K", description: "Chrome, neon glow, retro-future", mode: "dark" },
]

interface ThemeState {
  theme: ThemeId
  setTheme: (theme: ThemeId) => void
}

const stored = localStorage.getItem("app_theme") as ThemeId | null

export const useThemeStore = create<ThemeState>((set) => ({
  theme: stored && THEMES.some((t) => t.id === stored) ? stored : "linear",
  setTheme: (theme) => {
    localStorage.setItem("app_theme", theme)
    set({ theme })
  },
}))
