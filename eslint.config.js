import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

export default [
  // Global ignores
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      '**/coverage/**',
      'reports/**',
      '**/*.d.ts',
    ],
  },

  // Base JS rules
  js.configs.recommended,

  // TypeScript rules for libs
  ...tseslint.configs.recommended.map(config => ({
    ...config,
    files: ['libs/**/*.ts', 'libs/**/*.tsx'],
  })),

  // Vue + TypeScript rules for console
  ...pluginVue.configs['flat/recommended'].map(config => ({
    ...config,
    files: ['ui/wip-console/src/**/*.vue', 'ui/wip-console/src/**/*.ts'],
  })),

  // TypeScript-specific overrides
  {
    files: ['libs/**/*.ts', 'libs/**/*.tsx', 'ui/wip-console/src/**/*.ts'],
    rules: {
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },

  // Vue-specific overrides
  {
    files: ['ui/wip-console/src/**/*.vue'],
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/no-unused-vars': 'warn',
    },
  },
]
