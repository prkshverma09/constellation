import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1f2937",
        paper: "#f8fafc",
        accent: "#435b8c",
      },
    },
  },
  plugins: [],
};

export default config;
