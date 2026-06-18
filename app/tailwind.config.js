/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // DeepCode accent (matches renderer.py ACCENT_PRESETS); overridable
        // at runtime via a CSS variable so /color theming can drive it later.
        accent: "var(--accent, #d77757)",
        ink: "#0c0c0f",
        panel: "#141418",
        edge: "#26262e",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
