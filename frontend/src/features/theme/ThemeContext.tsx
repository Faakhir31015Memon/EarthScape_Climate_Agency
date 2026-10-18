import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

type Theme = 'light' | 'dark'

interface ThemeState {
  theme: Theme
  toggle: () => void
  setTheme: (theme: Theme) => void
  density: 'comfortable' | 'compact'
  setDensity: (density: 'comfortable' | 'compact') => void
}

const ThemeContext = createContext<ThemeState | null>(null)
const STORAGE_KEY = 'earthscape.theme'
const DENSITY_KEY = 'earthscape.density'

function initialTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function initialDensity(): 'comfortable' | 'compact' {
  try {
    return window.localStorage.getItem(DENSITY_KEY) === 'compact' ? 'compact' : 'comfortable'
  } catch {
    return 'comfortable'
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)
  const [density, setDensityState] = useState<'comfortable' | 'compact'>(initialDensity)

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.toggle('dark', theme === 'dark')
    root.style.colorScheme = theme
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  useEffect(() => {
    rootDensity(density)
    try {
      window.localStorage.setItem(DENSITY_KEY, density)
    } catch {
      /* ignore */
    }
  }, [density])

  const setTheme = useCallback((next: Theme) => setThemeState(next), [])
  const toggle = useCallback(() => setThemeState((current) => (current === 'dark' ? 'light' : 'dark')), [])
  const setDensity = useCallback((next: 'comfortable' | 'compact') => setDensityState(next), [])

  const value = useMemo(() => ({ theme, toggle, setTheme, density, setDensity }), [theme, toggle, setTheme, density, setDensity])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

function rootDensity(density: 'comfortable' | 'compact'): void {
  window.document.documentElement.dataset.density = density
}

export function useTheme(): ThemeState {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used inside ThemeProvider')
  return context
}
