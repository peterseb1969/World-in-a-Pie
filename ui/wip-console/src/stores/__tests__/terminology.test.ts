import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTerminologyStore } from '../terminology'
import { useNamespaceStore } from '../namespace'

// Mock the API client
vi.mock('@/api/client', () => ({
  defStoreClient: {
    listTerminologies: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getTerminology: vi.fn(),
    createTerminology: vi.fn(),
    updateTerminology: vi.fn(),
    deleteTerminology: vi.fn()
  },
  registryClient: {
    listNamespaceGroups: vi.fn().mockResolvedValue([])
  }
}))

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} }
  }
})()
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

describe('useTerminologyStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
  })

  describe('showWipSection', () => {
    it('returns false when current group is wip', () => {
      const namespaceStore = useNamespaceStore()
      const store = useTerminologyStore()

      namespaceStore.setCurrentGroup('wip')

      expect(store.showWipSection).toBe(false)
    })

    it('returns true for open namespace that is not wip', () => {
      const namespaceStore = useNamespaceStore()
      const store = useTerminologyStore()

      // Set up a non-wip group with open isolation
      namespaceStore.groups = [{
        prefix: 'dev',
        description: 'Development',
        isolation_mode: 'open',
        allowed_external_refs: [],
        status: 'active',
        created_at: '2024-01-01',
        created_by: null,
        updated_at: '2024-01-01',
        updated_by: null,
        terminologies_ns: 'dev-terminologies',
        terms_ns: 'dev-terms',
        templates_ns: 'dev-templates',
        documents_ns: 'dev-documents',
        files_ns: 'dev-files'
      }]
      namespaceStore.setCurrentGroup('dev')

      expect(store.showWipSection).toBe(true)
    })

    it('returns false for strict isolation mode', () => {
      const namespaceStore = useNamespaceStore()
      const store = useTerminologyStore()

      // Set up a strict group
      namespaceStore.groups = [{
        prefix: 'private',
        description: 'Private',
        isolation_mode: 'strict',
        allowed_external_refs: [],
        status: 'active',
        created_at: '2024-01-01',
        created_by: null,
        updated_at: '2024-01-01',
        updated_by: null,
        terminologies_ns: 'private-terminologies',
        terms_ns: 'private-terms',
        templates_ns: 'private-templates',
        documents_ns: 'private-documents',
        files_ns: 'private-files'
      }]
      namespaceStore.setCurrentGroup('private')

      expect(store.showWipSection).toBe(false)
    })
  })

  describe('allTerminologies', () => {
    it('combines own and wip terminologies with namespace info', () => {
      const store = useTerminologyStore()

      store.ownTerminologies = [
        { terminology_id: 'TERM-001', code: 'GENDER', name: 'Gender', status: 'active', term_count: 3, created_at: '', updated_at: '' }
      ]
      store.wipTerminologies = [
        { terminology_id: 'TERM-002', code: 'COUNTRY', name: 'Country', status: 'active', term_count: 200, created_at: '', updated_at: '' }
      ]

      const all = store.allTerminologies

      expect(all).toHaveLength(2)
      expect(all[0]._namespace).toBe('wip-terminologies')
      expect(all[0]._isExternal).toBe(false)
      expect(all[1]._namespace).toBe('wip-terminologies')
      expect(all[1]._isExternal).toBe(true)
    })
  })
})
