import type { Config } from "tailwindcss";

// Colours are consumed as `rgb(var(--token) / <alpha-value>)` so the light/dark
// theme can be swapped by rewriting the CSS variables on <html data-theme>.
const withAlpha = (variable: string) =>
  `rgb(var(${variable}) / <alpha-value>)`;

export default {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: withAlpha("--c-bg"),
        surface: withAlpha("--c-surface"),
        "surface-2": withAlpha("--c-surface-2"),
        border: withAlpha("--c-border"),
        content: withAlpha("--c-text"),
        muted: withAlpha("--c-muted"),
        primary: {
          DEFAULT: withAlpha("--c-primary"),
          hover: withAlpha("--c-primary-hover"),
          fg: withAlpha("--c-primary-fg"),
          soft: withAlpha("--c-primary-soft"),
        },
        accent: withAlpha("--c-accent"),
        success: withAlpha("--c-success"),
        warning: withAlpha("--c-warning"),
        danger: withAlpha("--c-danger"),
        info: withAlpha("--c-info"),
        chart: {
          temperature: withAlpha("--c-chart-temperature"),
          precipitation: withAlpha("--c-chart-precipitation"),
          series3: withAlpha("--c-chart-series3"),
          series4: withAlpha("--c-chart-series4"),
          series5: withAlpha("--c-chart-series5"),
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      // Consistent 4px grid.
      spacing: {
        0.5: "2px",
        18: "4.5rem",
        88: "22rem",
        128: "32rem",
      },
      borderRadius: {
        card: "0.75rem",
      },
      boxShadow: {
        card: "0 1px 2px rgb(16 24 40 / 0.06), 0 1px 3px rgb(16 24 40 / 0.1)",
        pop: "0 8px 24px rgb(16 24 40 / 0.16)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in": {
          from: { transform: "translateX(100%)" },
          to: { transform: "translateX(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 150ms ease-out",
        "slide-in": "slide-in 200ms ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
