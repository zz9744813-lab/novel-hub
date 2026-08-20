export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Backgrounds — luminance stacking (brightened for visible layers)
        bg: {
          canvas:   "#0c0d10",
          panel:    "#131418",
          surface:  "#1c1e23",
          hover:    "#2a2d34",
          elevated: "#222529",
          base:     "#0a0b0e",
        },
        // Borders — visible white overlays
        border: {
          DEFAULT:  "rgba(255,255,255,0.08)",
          subtle:   "rgba(255,255,255,0.05)",
          standard: "rgba(255,255,255,0.10)",
          strong:   "rgba(255,255,255,0.16)",
        },
        // Text — four-tier
        text: {
          primary:   "#f5f6f8",
          secondary: "#c8cdd6",
          tertiary:  "#7a808c",
          disabled:  "#50545c",
        },
        // Brand — indigo-violet (brightened for dark bg)
        brand: {
          DEFAULT: "#6b7aff",
          accent:  "#8b8eff",
          hover:   "#a0a3ff",
          muted:   "rgba(107,122,255,0.14)",
        },
        // Semantic — low saturation
        success: { DEFAULT: "#27a644", muted: "rgba(39,166,68,0.12)" },
        warning: { DEFAULT: "#d4a24e", muted: "rgba(212,162,78,0.12)" },
        danger:  { DEFAULT: "#e05555", muted: "rgba(224,85,85,0.12)" },
        info:    { DEFAULT: "#5ba8ef", muted: "rgba(91,168,239,0.12)" },
      },
      fontFamily: {
        sans:  ["Inter", "system-ui", "-apple-system", "Noto Sans SC", "sans-serif"],
        serif: ["Noto Serif SC", "Georgia", "serif"],
        mono:  ["JetBrains Mono", "ui-monospace", "SF Mono", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px" }],
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