import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#f7f9fb",
          dim: "#d8dadc",
          bright: "#f7f9fb",
          "container-lowest": "#ffffff",
          "container-low": "#f2f4f6",
          container: "#eceef0",
          "container-high": "#e6e8ea",
          "container-highest": "#e0e3e5",
          tint: "#565e74",
          variant: "#e0e3e5",
        },
        "on-surface": {
          DEFAULT: "#191c1e",
          variant: "#45464d",
        },
        "inverse-surface": "#2d3133",
        "inverse-on-surface": "#eff1f3",
        primary: {
          DEFAULT: "#000000",
          container: "#131b2e",
          fixed: "#dae2fd",
          "fixed-dim": "#bec6e0",
        },
        "on-primary": {
          DEFAULT: "#ffffff",
          container: "#7c839b",
          fixed: "#131b2e",
          "fixed-variant": "#3f465c",
        },
        "inverse-primary": "#bec6e0",
        accent: {
          DEFAULT: "var(--accent)",
          2: "var(--accent-2)",
        },
        "on-accent": "var(--on-accent)",
        secondary: {
          DEFAULT: "#505f76",
          container: "#d0e1fb",
          fixed: "#d3e4fe",
          "fixed-dim": "#b7c8e1",
        },
        "on-secondary": {
          DEFAULT: "#ffffff",
          container: "#54647a",
          fixed: "#0b1c30",
          "fixed-variant": "#38485d",
        },
        tertiary: {
          DEFAULT: "#000000",
          container: "#002113",
          fixed: "#6ffbbe",
          "fixed-dim": "#4edea3",
        },
        "on-tertiary": {
          DEFAULT: "#ffffff",
          container: "#009668",
          fixed: "#002113",
          "fixed-variant": "#005236",
        },
        error: {
          DEFAULT: "#ba1a1a",
          container: "#ffdad6",
        },
        "on-error": {
          DEFAULT: "#ffffff",
          container: "#93000a",
        },
        outline: {
          DEFAULT: "#76777d",
          variant: "#c6c6cd",
        },
        background: "#f7f9fb",
        "on-background": "#191c1e",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        display: [
          "36px",
          { lineHeight: "44px", letterSpacing: "-0.02em", fontWeight: "700" },
        ],
        "headline-lg": [
          "24px",
          { lineHeight: "32px", letterSpacing: "-0.01em", fontWeight: "600" },
        ],
        "headline-md": [
          "18px",
          { lineHeight: "24px", fontWeight: "600" },
        ],
        "body-lg": [
          "16px",
          { lineHeight: "24px", fontWeight: "400" },
        ],
        "body-md": [
          "14px",
          { lineHeight: "20px", fontWeight: "400" },
        ],
        "data-tabular": [
          "13px",
          { lineHeight: "18px", fontWeight: "500" },
        ],
        "label-caps": [
          "12px",
          { lineHeight: "16px", letterSpacing: "0.05em", fontWeight: "600" },
        ],
        "mono-label": [
          "12px",
          { lineHeight: "16px", fontWeight: "400" },
        ],
      },
      borderRadius: {
        sm: "0.125rem",
        DEFAULT: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
        "2xl": "1rem",
        bubble: "12px",
        pill: "9999px",
      },
      spacing: {
        base: "4px",
        xs: "4px",
        "space-sm": "8px",
        "space-md": "16px",
        "space-lg": "24px",
        "space-xl": "32px",
        gutter: "20px",
        sidebar: "260px",
        "right-panel": "320px",
      },
      boxShadow: {
        "card": "0 1px 3px 0 rgba(0,0,0,0.04), 0 1px 2px -1px rgba(0,0,0,0.03)",
        "card-hover": "0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -2px rgba(0,0,0,0.03)",
        "dropdown": "0 4px 16px -2px rgba(0,0,0,0.08), 0 2px 6px -2px rgba(0,0,0,0.04)",
        "input-focus": "0 0 0 3px rgba(0,0,0,0.06)",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "0.3" },
          "50%": { opacity: "1" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in-left": {
          from: { opacity: "0", transform: "translateX(-12px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
      },
      animation: {
        "pulse-dot": "pulse-dot 1.4s ease-in-out infinite",
        "fade-in": "fade-in 0.3s ease-out",
        "slide-in-left": "slide-in-left 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
export default config;
