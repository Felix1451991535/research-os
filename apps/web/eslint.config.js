import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts", "public/sw.js"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.strict],
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      complexity: ["error", 12],
      "max-lines": ["error", { max: 400, skipBlankLines: false, skipComments: false }],
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
);
