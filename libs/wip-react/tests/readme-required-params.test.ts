/**
 * A mutation hook's variables and the README row that documents it must agree
 * on which parameters are required.
 *
 * The coverage guard (readme-hook-coverage.test.ts) checks names only, and a
 * hook whose `namespace` became required kept an optional `namespace?` in its
 * README row through two re-pins: the row looked reviewed and the example
 * copied from it failed at the service, which refuses an upload naming no
 * namespace. This reads the `UseMutationOptions` variables type of each hook
 * and compares required-ness with the Input column of its table row.
 *
 * A hook is compared when both sides are readable: its variables type is an
 * object literal or an interface in this package, and its README row shows a
 * `{ ... }` list. A row that names an imported request type instead
 * (`CreateDocumentRequest`) defers to that type's own definition.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, it, expect } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const hooksDir = join(here, '..', 'src', 'hooks')
const readme = readFileSync(join(here, '..', 'README.md'), 'utf8')

/** Properties of an object type: name -> optional. */
type Props = Map<string, boolean>

function hookFiles(): string[] {
  return readdirSync(hooksDir)
    .filter((f) => f.endsWith('.ts'))
    .map((f) => readFileSync(join(hooksDir, f), 'utf8'))
    .map(stripComments)
}

/** Comments would otherwise sit between a generic's comma and its argument. */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
}

/** `export interface X { a: T; b?: T }` → name -> props. */
function declaredInterfaces(sources: string[]): Map<string, Props> {
  const out = new Map<string, Props>()
  for (const source of sources) {
    for (const match of source.matchAll(/export interface (\w+)\s*\{/g)) {
      const body = balanced(source, match.index! + match[0].length - 1, '{', '}')
      if (body) out.set(match[1], parseProps(body))
    }
  }
  return out
}

/** The substring from the open bracket at `start` to its matching close. */
function balanced(text: string, start: number, open: string, close: string): string | null {
  let depth = 0
  for (let i = start; i < text.length; i++) {
    if (text[i] === open) depth++
    else if (text[i] === close) {
      depth--
      if (depth === 0) return text.slice(start + 1, i)
    }
  }
  return null
}

/** Split on separators at bracket depth 0. */
function topLevel(text: string, separators: string): string[] {
  const parts: string[] = []
  let depth = 0
  let current = ''
  for (const ch of text) {
    if ('<([{'.includes(ch)) depth++
    else if ('>)]}'.includes(ch)) depth--
    if (depth === 0 && separators.includes(ch)) {
      parts.push(current)
      current = ''
      continue
    }
    current += ch
  }
  parts.push(current)
  return parts
}

function parseProps(objectBody: string): Props {
  const props: Props = new Map()
  for (const entry of topLevel(objectBody, ';,\n')) {
    const named = /^\s*(\w+)\s*(\?)?\s*:/.exec(entry)
    if (named) props.set(named[1], named[2] === '?')
  }
  return props
}

/** hook name -> required-ness of each variable, for hooks this package types itself. */
function documentedVariables(): Map<string, Props> {
  const sources = hookFiles()
  const interfaces = declaredInterfaces(sources)
  const out = new Map<string, Props>()
  for (const source of sources) {
    for (const decl of source.matchAll(/export function (use[A-Z]\w*)\(/g)) {
      const name = decl[1]
      const rest = source.slice(decl.index!)
      const next = rest.search(/\nexport (function|const|interface|type)/)
      const block = next > 0 ? rest.slice(0, next) : rest
      const optionsAt = block.indexOf('UseMutationOptions<')
      if (optionsAt < 0) continue
      const args = balanced(block, optionsAt + 'UseMutationOptions'.length, '<', '>')
      if (!args) continue
      const variables = topLevel(args, ',')[2]
      if (!variables) continue
      const trimmed = variables.trim()
      if (trimmed.startsWith('{')) {
        out.set(name, parseProps(trimmed.slice(1, -1)))
      } else {
        const iface = /^(\w+)$/.exec(trimmed)
        if (iface && interfaces.has(iface[1])) out.set(name, interfaces.get(iface[1])!)
      }
    }
  }
  return out
}

/** `{ a, b? }` from a README table row's Input cell, as name -> optional. */
function readmeRowProps(hook: string): Props | null {
  const row = new RegExp('^\\| `' + hook + '\\([^|]*\\)` \\| `\\{([^}]*)\\}`', 'm')
  const match = row.exec(readme)
  if (!match) return null
  const props: Props = new Map()
  for (const entry of match[1].split(',')) {
    const named = /^\s*(\w+)(\??)\s*$/.exec(entry)
    if (named) props.set(named[1], named[2] === '?')
  }
  return props
}

const variables = documentedVariables()

describe('README mutation rows match their hook\'s required parameters', () => {
  const compared = [...variables.keys()].filter((hook) => readmeRowProps(hook) !== null)

  it('compares a meaningful number of hooks', () => {
    // Guards against a parser that quietly matches nothing.
    expect(compared.length).toBeGreaterThanOrEqual(10)
    // The hook whose required `namespace` this guard exists for, and whose
    // variables type sits under a comment line inside the generic.
    expect(compared).toContain('useUploadFile')
  })

  it.each(compared)('%s', (hook) => {
    const source = variables.get(hook)!
    const row = readmeRowProps(hook)!
    const drift: string[] = []
    for (const [prop, optional] of source) {
      if (!row.has(prop)) continue // the row may document a subset
      if (row.get(prop) !== optional) {
        drift.push(
          `${prop} is ${optional ? 'optional' : 'required'} in ${hook}'s variables type, ` +
            `the README row says ${row.get(prop) ? 'optional' : 'required'}`,
        )
      }
    }
    expect(drift, `README.md disagrees with the code: ${drift.join('; ')}`).toEqual([])
  })
})
