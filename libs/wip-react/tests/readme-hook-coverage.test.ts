/**
 * CASE-586 guard: the README must document every exported hook.
 *
 * The README's Read Hooks / Write Hooks / Key Hierarchy tables are
 * hand-maintained and had drifted ~5:1 (7 of ~40 mutation hooks listed). This
 * test turns that drift into a failing test: it imports the package's actual
 * runtime exports and asserts every `use*` hook name — and every top-level
 * `wipKeys` group — appears verbatim somewhere in README.md. A hook added
 * without a README row now fails CI instead of rotting silently.
 *
 * SCOPE (CASE-648): this guard checks NAME PRESENCE ONLY. It does not verify
 * that a documented signature or key shape matches the actual export — a row
 * with the right hook name but a stale parameter list (e.g. an added
 * `namespace` argument the README omits) passes. Signature/shape correctness
 * is not enforced here; treat a green run as "every hook is mentioned", not
 * "every documented signature is right".
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, it, expect } from 'vitest'

import * as wipReact from '../src/index'
import { wipKeys } from '../src/index'

const here = dirname(fileURLToPath(import.meta.url))
const readme = readFileSync(join(here, '..', 'README.md'), 'utf8')

const exportedHooks = Object.keys(wipReact)
  .filter((name) => /^use[A-Z]/.test(name))
  .sort()

describe('README hook coverage (CASE-586)', () => {
  it('documents every exported use* hook', () => {
    const missing = exportedHooks.filter(
      (hook) => !new RegExp(`\\b${hook}\\b`).test(readme),
    )
    expect(missing, `Hooks exported but absent from README.md: ${missing.join(', ')}`).toEqual([])
  })

  it('sanity-checks the export surface did not collapse', () => {
    // Guards against a regex/refactor that makes the coverage check vacuous.
    expect(exportedHooks.length).toBeGreaterThan(50)
  })

  it('documents every top-level wipKeys group in the Key Hierarchy', () => {
    const groups = Object.keys(wipKeys).filter((k) => k !== 'all')
    const missing = groups.filter(
      (g) => !new RegExp(`wipKeys\\.${g}\\b`).test(readme),
    )
    expect(missing, `wipKeys groups absent from README.md: ${missing.join(', ')}`).toEqual([])
  })
})
