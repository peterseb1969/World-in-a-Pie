#!/usr/bin/env tsx
/**
 * Generate TypeScript types from WIP OpenAPI specs.
 *
 * Usage:
 *   npx tsx scripts/generate-types.ts                         # from live services
 *   npx tsx scripts/generate-types.ts --from-cache            # from shared schemas/
 *   npx tsx scripts/generate-types.ts --base-url=http://host  # custom base URL
 *
 * The --from-cache flag reads from the shared schemas/ directory at the repo root.
 * This is the recommended workflow — run scripts/update-schemas.sh first, then
 * generate types from the cached specs. This ensures the MCP server (Python) and
 * @wip/client (TypeScript) always derive from the same OpenAPI snapshots.
 *
 * Requires: openapi-typescript (devDependency)
 */

import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))

const SERVICES = [
  { name: 'registry', port: 8001 },
  { name: 'def-store', port: 8002 },
  { name: 'template-store', port: 8003 },
  { name: 'document-store', port: 8004 },
  { name: 'reporting-sync', port: 8005 },
]

const SHARED_SCHEMA_DIR = resolve(__dirname, '../../../schemas')

async function main() {
  const fromCache = process.argv.includes('--from-cache')
  const baseUrl = process.argv.find((a) => a.startsWith('--base-url='))?.split('=')[1] ?? 'http://localhost'
  const outDir = resolve(__dirname, '../src/types/generated')

  if (!existsSync(outDir)) {
    mkdirSync(outDir, { recursive: true })
  }

  // Dynamic import — openapi-typescript is a devDependency
  const { default: openapiTS } = await import('openapi-typescript')

  for (const svc of SERVICES) {
    let source: string | URL
    if (fromCache) {
      const cacheFile = resolve(SHARED_SCHEMA_DIR, `${svc.name}.json`)
      if (!existsSync(cacheFile)) {
        console.error(`  Cache miss for ${svc.name}: ${cacheFile}`)
        continue
      }
      // openapiTS accepts a parsed object or a URL; pass the file content as parsed JSON
      console.log(`Reading ${svc.name} from schemas/${svc.name}.json...`)
      const spec = JSON.parse(readFileSync(cacheFile, 'utf-8'))
      try {
        const output = await openapiTS(spec)
        const outFile = `${outDir}/${svc.name}.ts`
        writeFileSync(outFile, output as string, 'utf-8')
        console.log(`  -> ${outFile}`)
      } catch (err) {
        console.error(`  Failed for ${svc.name}:`, (err as Error).message)
      }
    } else {
      const url = `${baseUrl}:${svc.port}/openapi.json`
      console.log(`Fetching ${url}...`)
      try {
        const output = await openapiTS(new URL(url))
        const outFile = `${outDir}/${svc.name}.ts`
        writeFileSync(outFile, output as string, 'utf-8')
        console.log(`  -> ${outFile}`)
      } catch (err) {
        console.error(`  Failed for ${svc.name}:`, (err as Error).message)
      }
    }
  }

  // Generate barrel export
  const barrel = SERVICES.map(
    (s) => `export * as ${s.name.replace(/-/g, '_')} from './${s.name}.js'`,
  ).join('\n')
  writeFileSync(`${outDir}/index.ts`, barrel + '\n', 'utf-8')
  console.log('Done.')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
