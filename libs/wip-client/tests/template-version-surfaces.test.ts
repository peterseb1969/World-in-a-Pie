import { describe, it, expect } from 'vitest'
import { asVersionEventDetails } from '../src/types/template'
import type {
  CreateTemplateRequest,
  CrossVersionView,
  ReportingConfig,
  Template,
  UpdateTemplateRequest,
} from '../src/types/template'

// CASE-720 — the backend accepts and persists `reporting.cross_version_view`
// and template `renames`, and reports version-event impact on both write
// paths. None of it was typed, so a consumer had to cast. The object
// literals below are compile-time guards: if a member were missing from the
// type, this file would not compile.

describe('reporting.cross_version_view (CASE-720)', () => {
  it('accepts a mapped column, an empty mapping, and null', () => {
    // All three forms are legal server-side. `{}` and `null` both mean
    // "keep this column's own name where it exists, NULL elsewhere" — the
    // identity-core passthrough; only `{ from }` renames.
    const view: CrossVersionView = {
      versions: 'all',
      columns: {
        k_alias: { from: 'k' },
        passthrough: {},
        also_passthrough: null,
      },
    }
    expect(view.columns.k_alias).toEqual({ from: 'k' })
    expect(view.columns.also_passthrough).toBeNull()
  })

  it('accepts an explicit version list', () => {
    const view: CrossVersionView = { versions: [1, 3], columns: {} }
    expect(view.versions).toEqual([1, 3])
  })

  it('hangs off ReportingConfig and may be omitted or nulled', () => {
    const withView: ReportingConfig = {
      sync_enabled: true,
      sync_strategy: 'latest_only',
      include_metadata: true,
      flatten_arrays: true,
      max_array_elements: 10,
      cross_version_view: { versions: 'all', columns: {} },
    }
    const without: ReportingConfig = {
      sync_enabled: true,
      sync_strategy: 'latest_only',
      include_metadata: true,
      flatten_arrays: true,
      max_array_elements: 10,
      cross_version_view: null,
    }
    expect(withView.cross_version_view?.versions).toBe('all')
    expect(without.cross_version_view).toBeNull()
  })
})

describe('template renames (CASE-720)', () => {
  it('is settable on create', () => {
    const req: CreateTemplateRequest = {
      value: 'PERSON',
      label: 'Person',
      namespace: 'wip',
      renames: { family_name: 'last_name' },
    }
    expect(req.renames).toEqual({ family_name: 'last_name' })
  })

  it('is settable on update — the path an editor takes', () => {
    // updateTemplate() posts to PUT, so renaming a field on an EXISTING
    // template goes through this model, not the create one.
    const req: UpdateTemplateRequest = {
      renames: { family_name: 'last_name' },
      fields: [],
    }
    expect(req.renames).toEqual({ family_name: 'last_name' })
  })

  it('comes back on read', () => {
    const tpl: Pick<Template, 'renames'> = { renames: { family_name: 'last_name' } }
    expect(tpl.renames?.family_name).toBe('last_name')
  })
})

describe('asVersionEventDetails (CASE-720)', () => {
  const diff = {
    added_optional: ['note'],
    added_required: [],
    removed: ['old_note'],
    changed_type: [],
    made_required: [],
    modified_existing: [],
    identity_changed: null,
    relationship_refs_changed: null,
  }

  it('narrows a version event carrying impact and migration', () => {
    const details = asVersionEventDetails({
      ...diff,
      impact: {
        status: 'ok',
        total_live_docs: 7,
        docs_per_version: { '1': 7 },
        field_nonempty_counts: { old_note: 0 },
      },
      migration: {
        eligible: true,
        reason: 'removed fields are empty in all live documents',
        via: 'migrate_documents (dry_run first)',
      },
    })
    expect(details).not.toBeNull()
    expect(details!.impact.total_live_docs).toBe(7)
    expect(details!.migration.eligible).toBe(true)
    expect(details!.removed).toEqual(['old_note'])
  })

  it('carries unavailable as a real answer, never a silent zero', () => {
    const details = asVersionEventDetails({
      ...diff,
      impact: { status: 'unavailable', reason: 'RuntimeError: unreachable' },
      migration: { eligible: null, reason: 'counts unavailable', via: 'migrate_documents' },
    })
    expect(details!.impact.status).toBe('unavailable')
    expect(details!.impact.total_live_docs).toBeUndefined()
    expect(details!.migration.eligible).toBeNull()
  })

  it('returns null for a plain diff — an older backend, or no version minted', () => {
    expect(asVersionEventDetails(diff)).toBeNull()
    expect(asVersionEventDetails(undefined)).toBeNull()
    expect(asVersionEventDetails(null)).toBeNull()
  })
})
