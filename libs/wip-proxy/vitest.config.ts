import { defineConfig } from 'vitest/config'

/**
 * The peer range spans two express majors whose routers parse route patterns
 * with incompatible path-to-regexp generations (0.1.x vs 8.x) — a pattern can
 * register cleanly on one major and throw at mount time on the other. Every
 * test therefore runs twice, once per major, via npm-aliased devDependencies
 * (`express4`, `express5`) mapped onto the bare `express` specifier the source
 * imports. Each project carries a canary test asserting which major it really
 * resolved, so a broken alias fails loudly instead of silently testing the
 * same express twice.
 */
function project(name: 'express4' | 'express5') {
  return {
    test: {
      name,
      env: { WIP_PROXY_TEST_EXPRESS_MAJOR: name === 'express4' ? '4' : '5' },
    },
    resolve: {
      alias: { express: name },
    },
  }
}

export default defineConfig({
  test: {
    projects: [project('express4'), project('express5')],
  },
})
