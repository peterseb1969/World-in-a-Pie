/** Query key factories for TanStack Query cache management. */
export const wipKeys = {
  all: ['wip'] as const,

  terminologies: {
    all: ['wip', 'terminologies'] as const,
    list: (params?: object) => ['wip', 'terminologies', 'list', params] as const,
    detail: (id: string) => ['wip', 'terminologies', 'detail', id] as const,
  },

  terms: {
    all: ['wip', 'terms'] as const,
    list: (terminologyId: string, params?: object) =>
      ['wip', 'terms', 'list', terminologyId, params] as const,
    detail: (id: string) => ['wip', 'terms', 'detail', id] as const,
  },

  templates: {
    all: ['wip', 'templates'] as const,
    list: (params?: object) => ['wip', 'templates', 'list', params] as const,
    detail: (id: string) => ['wip', 'templates', 'detail', id] as const,
    byValue: (value: string) => ['wip', 'templates', 'by-value', value] as const,
  },

  documents: {
    all: ['wip', 'documents'] as const,
    list: (params?: object) => ['wip', 'documents', 'list', params] as const,
    detail: (id: string) => ['wip', 'documents', 'detail', id] as const,
    versions: (id: string) => ['wip', 'documents', 'versions', id] as const,
    tableView: (templateId: string, params?: object) =>
      ['wip', 'documents', 'table', templateId, params] as const,
  },

  files: {
    all: ['wip', 'files'] as const,
    list: (params?: object) => ['wip', 'files', 'list', params] as const,
    detail: (id: string) => ['wip', 'files', 'detail', id] as const,
    downloadUrl: (id: string) => ['wip', 'files', 'download-url', id] as const,
  },

  registry: {
    all: ['wip', 'registry'] as const,
    namespaces: () => ['wip', 'registry', 'namespaces'] as const,
    namespace: (prefix: string) => ['wip', 'registry', 'namespaces', prefix] as const,
    entries: (params?: object) => ['wip', 'registry', 'entries', params] as const,
    entry: (id: string) => ['wip', 'registry', 'entries', id] as const,
    search: (params?: object) => ['wip', 'registry', 'search', params] as const,
  },

  reporting: {
    all: ['wip', 'reporting'] as const,
    integrity: (params?: object) => ['wip', 'reporting', 'integrity', params] as const,
    activity: (params?: object) => ['wip', 'reporting', 'activity', params] as const,
    search: (params?: object) => ['wip', 'reporting', 'search', params] as const,
    query: (sql: string, params?: unknown[]) => ['wip', 'reporting', 'query', sql, params] as const,
  },
} as const
