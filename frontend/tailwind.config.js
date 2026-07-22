export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0d0e11",
          900: "#121417",
          850: "#191c20",
          800: "#1e2228",
          700: "#282d35",
          600: "#3a4049",
          500: "#525964",
          400: "#717a87",
          300: "#9ba3af",
        },
        accent: {
          DEFAULT: "#d4a574",
          dim: "#8a6d4a",
          bright: "#e8c498",
        },
        teal: {
          accent: "#5b9b8f",
        },
        sage: {
          DEFAULT: "#7a9e7e",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Noto Sans SC", "sans-serif"],
        serif: ["Noto Serif SC", "Georgia", "serif"],
        mono: ["JetBrains Mono", "SF Mono", "monospace"],
      },
      animation: {
        "fade-in": "fadeIn 0.3s ease-out",
        "slide-up": "slideUp 0.4s ease-out",
        "pulse-soft": "pulseSoft 2s ease-in-out infinite",
      },
      keyframes: {
        fadeIn: { "0%": "{ opacity: 0 }", "100%": "{ opacity: 1 }" },
        slideUp: { "0%": "{ transform: translateY(8px); opacity: 0 }", "100%": "{ transform: translateY(0); opacity: 1 }" },
        pulseSoft: { "0%, 100%": "{ opacity: 0.6 }", "50%": "{ opacity: 1 }" },
      },
    },
  },
  plugins: [],
};
