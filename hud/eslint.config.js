// ESLint: TypeScript-Regeln, React-Hooks und ein Verbot von HTML aus Zeichenketten.
// Modell- und Werkzeugtext darf nie als HTML in die Seite (wie bei der Web-Oberfläche, ADR-010).
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src-tauri"] },
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Kein HTML aus Zeichenketten: Text nur als Text einfügen.",
        },
        {
          selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]",
          message: "Kein innerHTML/outerHTML: Text nur als Text einfügen.",
        },
        {
          selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
          message: "Kein insertAdjacentHTML.",
        },
      ],
    },
  },
);
