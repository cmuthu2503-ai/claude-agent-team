import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeId =
  | 'vercel'
  | 'cyberpunk-hyperdrive';

export type ThemeMode = 'light' | 'dark';

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  description: string;
  /** [background, primary, accent, glow/ring] — swatch preview. */
  swatch: [string, string, string, string];
}

export const THEMES: ThemeMeta[] = [
  {
    id: 'vercel',
    label: 'Vercel',
    description: 'Monochrome black-and-white precision.',
    swatch: ['#000000', '#FFFFFF', '#888888', '#FFFFFF'],
  },
  {
    id: 'cyberpunk-hyperdrive',
    label: 'Cyberpunk Hyperdrive',
    description: 'Cyan / matrix-green neon grid with scanlines and glow.',
    swatch: ['#0A0014', '#00F0FF', '#39FF14', '#F9F871'],
  },
];

const VALID_THEME_IDS: ReadonlySet<string> = new Set(
  THEMES.map((t) => t.id),
);

const DEFAULT_THEME: ThemeId = 'cyberpunk-hyperdrive';
const DEFAULT_MODE: ThemeMode = 'dark';

interface ThemeState {
  theme: ThemeId;
  mode: ThemeMode;
  setTheme: (theme: ThemeId) => void;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
}

export function applyDomAttributes(
  theme: ThemeId,
  mode: ThemeMode,
): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  root.setAttribute('data-mode', mode);
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: DEFAULT_THEME,
      mode: DEFAULT_MODE,
      setTheme: (theme) => {
        set({ theme });
        applyDomAttributes(theme, get().mode);
      },
      setMode: (mode) => {
        set({ mode });
        applyDomAttributes(get().theme, mode);
      },
      toggleMode: () => {
        const next: ThemeMode =
          get().mode === 'dark' ? 'light' : 'dark';
        set({ mode: next });
        applyDomAttributes(get().theme, next);
      },
    }),
    {
      name: 'agent-team-theme',
      onRehydrateStorage: () => (state) => {
        if (!state) {
          // No persisted state — still ensure DOM has defaults.
          applyDomAttributes(DEFAULT_THEME, DEFAULT_MODE);
          return;
        }
        if (!VALID_THEME_IDS.has(state.theme)) {
          state.theme = DEFAULT_THEME;
        }
        if (state.mode !== 'light' && state.mode !== 'dark') {
          state.mode = DEFAULT_MODE;
        }
        applyDomAttributes(state.theme, state.mode);
      },
    },
  ),
);

// Module-init: guarantee <html> has data-theme/data-mode on first
// load, even before rehydration completes or if no persisted state
// exists. Subsequent rehydrate callback will overwrite with the
// persisted (validated) values.
applyDomAttributes(
  useThemeStore.getState().theme,
  useThemeStore.getState().mode,
);
