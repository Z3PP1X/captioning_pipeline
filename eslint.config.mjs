import tseslint from 'typescript-eslint';
import angular from '@angular-eslint/eslint-plugin';
import angularTemplate from '@angular-eslint/eslint-plugin-template';
import angularTemplateParser from '@angular-eslint/template-parser';
import sonarjs from 'eslint-plugin-sonarjs';

/**
 * UX gate (see `make ux-gate`): accessibility + ergonomics rules for every Angular template. The app uses
 * inline `template:` strings, so the `extract-inline-html` processor feeds them to the template parser as
 * virtual `*.html` files; standalone `.html` templates get the same rules.
 */
const uxTemplateRules = {
  '@angular-eslint/template/click-events-have-key-events': 'error',
  '@angular-eslint/template/interactive-supports-focus': 'error',
  '@angular-eslint/template/alt-text': 'error',
  '@angular-eslint/template/no-autofocus': 'error',
  '@angular-eslint/template/no-positive-tabindex': 'error',
};

export default [
  {
    ignores: [
      'dist/**',
      '.angular/**',
      'node_modules/**',
      'src/web/dist/**',
      'src/web/shared/**',
      'scripts/**',
      'frontend/**',
      'backend/**',
      '*.config.js',
      '*.config.mjs',
    ],
  },
  // TypeScript & Angular Component rules
  {
    files: ['src/web/**/*.ts'],
    processor: angularTemplate.processors['extract-inline-html'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        project: ['./tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      '@typescript-eslint': tseslint.plugin,
      '@angular-eslint': angular,
      sonarjs,
    },
    rules: {
      // Hard complexity thresholds
      'complexity': ['error', { max: 10 }],
      'sonarjs/cognitive-complexity': ['error', 15],
      'max-lines-per-function': ['error', { max: 75, skipBlankLines: true, skipComments: true }],
      'max-depth': ['error', 3],
      // UX gate: smooth (60 FPS) rendering of large image grids
      '@angular-eslint/prefer-on-push-component-change-detection': 'error',

      // UX gate: design-token discipline inside inline templates (the CSS side is enforced by stylelint)
      'no-restricted-syntax': [
        'error',
        {
          selector: 'TemplateElement[value.raw=/[a-z]-\\[#[0-9a-fA-F]{3,8}\\]/]',
          message: 'Hard-coded hex colour in a Tailwind class: use a design token (src/web/styles/_tokens.css).',
        },
        {
          selector:
            'TemplateElement[value.raw=/(^|[\\s"\'])-?(p|m|px|py|pt|pb|pl|pr|ps|pe|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y)-\\[(?!var\\(--space-)/]',
          message: 'Arbitrary spacing value breaks the 4px grid: use p-1/gap-2/... or var(--space-N).',
        },
      ],

      // Type safety & quality guards
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/consistent-type-definitions': ['error', 'interface'],
    },
  },
  // Relaxed function length for unit tests
  {
    files: ['src/web/**/*.spec.ts'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        project: ['./tsconfig.spec.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      'max-lines-per-function': 'off',
      'complexity': 'off',
      'sonarjs/cognitive-complexity': 'off',
      'max-depth': 'off',
    },
  },
  // Angular HTML templates: external `.html` files and the inline templates extracted by the processor
  {
    files: ['src/web/**/*.html'],
    languageOptions: {
      parser: angularTemplateParser,
    },
    plugins: {
      '@angular-eslint/template': angularTemplate,
    },
    rules: uxTemplateRules,
  },
];
