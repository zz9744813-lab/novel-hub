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
        // Ink accent — warm amber, echoes the novel-reader paper tone
        ink: {
          DEFAULT: "#d4a574",
          soft:    "#e8c79a",
          muted:   "rgba(212,165,116,0.14)",
        },
        // Semantic — low saturation
        success: { DEFAULT: "#27a644", muted: "rgba(39,166,68,0.12)" },
        warning: { DEFAULT: "#d4a24e", muted: "rgba(212,162,78,0.12)" },
        danger:  { DEFAULT: "#e05555", muted: "rgba(224,85,85,0.12)" },
        info:    { DEFAULT: "#5ba8ef", muted: "rgba(91,168,239,0.12)" },
      },
      boxShadow: {
        "glow":          "0 0 16px rgba(107,122,255,0.35)",
        "glow-accent":   "0 0 10px rgba(139,142,255,0.55)",
        "card":          "0 1px 2px rgba(0,0,0,0.25), 0 8px 24px rgba(0,0,0,0.25)",
        "card-hover":    "0 2px 4px rgba(0,0,0,0.3), 0 16px 40px rgba(0,0,0,0.38)",
        "modal":         "0 24px 80px rgba(0,0,0,0.55)",
      },
      fontFamily: {
        sans:  ["Inter", "system-ui", "-apple-system", "Noto Sans SC", "sans-serif"],
        serif: ["Noto Serif SC", "Georgia", "serif"],
        mono:  ["JetBrains Mono", "ui-monospace", "SF Mono", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px" }],
        // Design baseline scale (v8.1): caption -> h1
        caption:  ["11px", { lineHeight: "16px" }],
        body:     ["13px", { lineHeight: "19px" }],
        emphasis: ["15px", { lineHeight: "22px" }],
        h2:       ["20px", { lineHeight: "27px", fontWeight: "590" }],
        h1:       ["28px", { lineHeight: "35px", fontWeight: "600" }],
      },
      borderRadius: {
        // Design baseline radius (v8.1): control / card / pill
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