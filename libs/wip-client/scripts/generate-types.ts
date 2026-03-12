#!/usr/bin/env tsx
/**
 * Fetch OpenAPI specs from running WIP services and generate TypeScript types.
 *
 * Usage:
 *   npx tsx scripts/generate-types.ts [--base-url http://localhost]
 *
 * Requires: openapi-typescript (devDependency)
 */

import { writeFileSync, mkdirSync, existsSync } from 'fs'

const SERVICES = [
  { name: 'registry', port: 8001 },
  { name: 'def-store', port: 8002 },
  { name: 'template-store', port: 8003 },
  { name: 'document-store', port: 8004 },
  { name: 'reporting-sync', port: 8005 },
]

async function main() {
  const baseUrl = process.argv.find((a) => a.startsWith('--base-url='))?.split('=')[1] ?? 'http://localhost'
  const outDir = new URL('../src/types/generated', import.meta.url).pathname

  if (!existsSync(outDir)) {
    mkdirSync(outDir, { recursive: true })
  }

  // Dynamic import — openapi-typescript is a devDependency
  const { default: openapiTS } = await import('openapi-typescript')

  for (const svc of SERVICES) {
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
