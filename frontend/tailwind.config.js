export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic tokens — one palette per theme, driven by CSS variables
        // (v9.5 spec §93–§95). No fixed hex here.
        bg: {
          canvas:   "rgb(var(--nf-bg-canvas) / <alpha-value>)",
          panel:    "rgb(var(--nf-bg-panel) / <alpha-value>)",
          surface:  "rgb(var(--nf-bg-surface) / <alpha-value>)",
          hover:    "rgb(var(--nf-bg-hover) / <alpha-value>)",
          elevated: "rgb(var(--nf-bg-elevated) / <alpha-value>)",
          base:     "rgb(var(--nf-bg-canvas) / <alpha-value>)",
          input:    "rgb(var(--nf-bg-input) / <alpha-value>)",
        },
        border: {
          DEFAULT:  "rgb(var(--nf-border) / var(--nf-border-standard-alpha))",
          subtle:   "rgb(var(--nf-border) / var(--nf-border-subtle-alpha))",
          standard: "rgb(var(--nf-border) / var(--nf-border-standard-alpha))",
          strong:   "rgb(var(--nf-border) / var(--nf-border-strong-alpha))",
        },
        text: {
          primary:   "rgb(var(--nf-text-primary) / <alpha-value>)",
          secondary: "rgb(var(--nf-text-secondary) / <alpha-value>)",
          tertiary:  "rgb(var(--nf-text-tertiary) / <alpha-value>)",
          disabled:  "rgb(var(--nf-text-disabled) / <alpha-value>)",
          inverse:   "rgb(var(--nf-text-inverse) / <alpha-value>)",
        },
        brand: {
          DEFAULT: "rgb(var(--nf-brand) / <alpha-value>)",
          accent:  "rgb(var(--nf-brand-accent) / <alpha-value>)",
          hover:   "rgb(var(--nf-brand-hover) / <alpha-value>)",
          muted:   "rgb(var(--nf-brand) / 0.14)",
        },
        ink: {
          DEFAULT: "rgb(var(--nf-ink) / <alpha-value>)",
          soft:    "rgb(var(--nf-ink-soft) / <alpha-value>)",
          muted:   "rgb(var(--nf-ink) / 0.14)",
        },
        success: { DEFAULT: "rgb(var(--nf-success) / <alpha-value>)", muted: "rgb(var(--nf-success) / 0.12)" },
        warning: { DEFAULT: "rgb(var(--nf-warning) / <alpha-value>)", muted: "rgb(var(--nf-warning) / 0.12)" },
        danger:  { DEFAULT: "rgb(var(--nf-danger) / <alpha-value>)",  muted: "rgb(var(--nf-danger) / 0.12)" },
        info:    { DEFAULT: "rgb(var(--nf-info) / <alpha-value>)",    muted: "rgb(var(--nf-info) / 0.12)" },
      },
      boxShadow: {
        "glow":          "0 0 16px rgb(var(--nf-brand) / 0.35)",
        "glow-accent":   "0 0 10px rgb(var(--nf-brand-accent) / 0.55)",
        "card":          "0 1px 2px rgb(0 0 0 / var(--nf-shadow-strong, 0.25)), 0 8px 24px rgb(0 0 0 / var(--nf-shadow-strong, 0.25))",
        "card-hover":    "0 2px 4px rgb(0 0 0 / 0.3), 0 16px 40px rgb(0 0 0 / 0.38)",
        "modal":         "0 24px 80px rgb(0 0 0 / 0.55)",
      },
      fontFamily: {
        sans:  ["Inter", "system-ui", "-apple-system", "Noto Sans SC", "sans-serif"],
        serif: ["Noto Serif SC", "Georgia", "serif"],
        mono:  ["JetBrains Mono", "ui-monospace", "SF Mono", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px" }],
        caption:  ["11px", { lineHeight: "16px" }],
        body:     ["13px", { lineHeight: "19px" }],
        emphasis: ["15px", { lineHeight: "22px" }],
        h2:       ["20px", { lineHeight: "27px", fontWeight: "590" }],
        h1:       ["28px", { lineHeight: "35px", fontWeight: "600" }],
      },
      borderRadius: {
        control: "8px",
        card: "14px",
      },
      animation: {
        "fade-in":   "fadeIn 0.2s ease-out",
        "slide-up":  "slideUp 0.2s ease-out",
        "page-in":   "pageIn 0.2s ease-out",
      },
      keyframes: {
        fadeIn:  { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        slideUp: { "0%": { transform: "translateY(6px)", opacity: "0" }, "100%": { transform: "translateY(0)", opacity: "1" } },
        pageIn:  { "0%": { transform: "translateY(6px)", opacity: "0" }, "100%": { transform: "translateY(0)", opacity: "1" } },
      },
    },
  },
  plugins: [],
};
