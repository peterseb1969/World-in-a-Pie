import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useNamespaceStore } from '../namespace'

// Mock the API client
vi.mock('@/api/client', () => ({
  registryClient: {
    listNamespaceGroups: vi.fn().mockResolvedValue([]),
    createNamespaceGroup: vi.fn(),
    archiveNamespaceGroup: vi.fn(),
    restoreNamespaceGroup: vi.fn(),
    getNamespaceGroupStats: vi.fn()
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

describe('useNamespaceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
  })

  describe('computed namespace IDs', () => {
    it('derives correct namespace IDs from default group', () => {
      const store = useNamespaceStore()

      expect(store.currentGroup).toBe('wip')
      expect(store.terminologiesNs).toBe('wip-terminologies')
      expect(store.termsNs).toBe('wip-terms')
      expect(store.templatesNs).toBe('wip-templates')
      expect(store.documentsNs).toBe('wip-documents')
      expect(store.filesNs).toBe('wip-files')
    })

    it('derives correct namespace IDs after group change', () => {
      const store = useNamespaceStore()

      store.setCurrentGroup('dev')

      expect(store.currentGroup).toBe('dev')
      expect(store.terminologiesNs).toBe('dev-terminologies')
      expect(store.termsNs).toBe('dev-terms')
      expect(store.templatesNs).toBe('dev-templates')
      expect(store.documentsNs).toBe('dev-documents')
      expect(store.filesNs).toBe('dev-files')
    })
  })

  describe('setCurrentGroup', () => {
    it('updates currentGroup and persists to localStorage', () => {
      const store = useNamespaceStore()

      store.setCurrentGroup('staging')

      expect(store.currentGroup).toBe('staging')
      expect(localStorageMock.getItem('wip-namespace-group')).toBe('staging')
    })
  })

  describe('isNonProduction', () => {
    it('returns false for wip namespace', () => {
      const store = useNamespaceStore()

      expect(store.isNonProduction).toBe(false)
    })

    it('returns true for non-wip namespace', () => {
      const store = useNamespaceStore()

      store.setCurrentGroup('dev')

      expect(store.isNonProduction).toBe(true)
    })
  })

  describe('currentGroupData', () => {
    it('returns null when groups are empty', () => {
      const store = useNamespaceStore()

      expect(store.currentGroupData).toBe(null)
    })

    it('returns matching group when found', () => {
      const store = useNamespaceStore()
      const mockGroup = {
        prefix: 'wip',
        description: 'Production',
        isolation_mode: 'open' as const,
        allowed_external_refs: [],
        status: 'active' as const,
        created_at: '2024-01-01',
        created_by: 'admin',
        updated_at: '2024-01-01',
        updated_by: null,
        terminologies_ns: 'wip-terminologies',
        terms_ns: 'wip-terms',
        templates_ns: 'wip-templates',
        documents_ns: 'wip-documents',
        files_ns: 'wip-files'
      }

      store.groups = [mockGroup]

      expect(store.currentGroupData).toEqual(mockGroup)
    })
  })
})
