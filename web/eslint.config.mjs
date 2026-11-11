import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'playwright-report/**', 'test-results/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { parser: tseslint.parser },
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: {
      'no-debugger': 'error',
      'no-duplicate-imports': ['error', { allowSeparateTypeImports: true }],
      '@typescript-eslint/no-unsafe-declaration-merging': 'error',
      '@typescript-eslint/no-unnecessary-type-constraint': 'error',
    },
  },
);
