export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Backgrounds — luminance stacking
        bg: {
          canvas:   "#08090a",
          panel:    "#0f1011",
          surface:  "#191a1b",
          hover:    "#28282c",
        },
        // Borders — semi-transparent white
        border: {
          DEFAULT:  "rgba(255,255,255,0.06)",
          subtle:   "rgba(255,255,255,0.04)",
          standard: "rgba(255,255,255,0.08)",
          strong:   "rgba(255,255,255,0.12)",
        },
        // Text — four-tier
        text: {
          primary:   "#f7f8f8",
          secondary: "#d0d6e0",
          tertiary:  "#8a8f98",
          disabled:  "#62666d",
        },
        // Brand — indigo-violet
        brand: {
          DEFAULT: "#5e6ad2",
          accent:  "#7170ff",
          hover:   "#828fff",
          muted:   "rgba(94,106,210,0.12)",
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
