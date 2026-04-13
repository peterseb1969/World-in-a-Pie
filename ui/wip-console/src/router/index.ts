import { createRouter, createWebHistory } from 'vue-router'
import { useNamespaceStore } from '@/stores'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue')
    },
    // Auth routes (OIDC callbacks)
    {
      path: '/auth/callback',
      name: 'auth-callback',
      component: () => import('@/views/auth/AuthCallback.vue'),
      meta: { layout: 'none' }
    },
    {
      path: '/auth/silent-renew',
      name: 'auth-silent-renew',
      component: () => import('@/views/auth/SilentRenew.vue'),
      meta: { layout: 'none' }
    },
    // Terminology routes
    {
      path: '/terminologies',
      name: 'terminologies',
      component: () => import('@/views/terminologies/TerminologyListView.vue')
    },
    {
      path: '/terminologies/import',
      name: 'terminology-import',
      component: () => import('@/views/terminologies/ImportView.vue'),
      meta: { requiresWrite: true }
    },
    {
      path: '/terminologies/validate',
      name: 'terminology-validate',
      component: () => import('@/views/terminologies/ValidateView.vue')
    },
    {
      path: '/ontology',
      name: 'ontology-browser',
      component: () => import('@/views/terminologies/OntologyBrowserView.vue')
    },
    {
      path: '/terminologies/:id',
      name: 'terminology-detail',
      component: () => import('@/views/terminologies/TerminologyDetailView.vue'),
      props: true
    },
    {
      path: '/terms/:id',
      name: 'term-detail',
      component: () => import('@/views/terminologies/TermDetailView.vue'),
      props: true
    },
    // Template routes
    {
      path: '/templates',
      name: 'templates',
      component: () => import('@/views/templates/TemplateListView.vue')
    },
    {
      path: '/templates/new',
      name: 'template-create',
      component: () => import('@/views/templates/TemplateDetailView.vue'),
      meta: { requiresWrite: true }
    },
    {
      path: '/templates/:id',
      name: 'template-detail',
      component: () => import('@/views/templates/TemplateDetailView.vue'),
      props: true
    },
    // Document routes
    {
      path: '/documents',
      name: 'documents',
      component: () => import('@/views/documents/DocumentListView.vue')
    },
    {
      path: '/documents/import',
      name: 'document-import',
      component: () => import('@/views/documents/ImportView.vue'),
      meta: { requiresWrite: true }
    },
    {
      path: '/documents/new',
      name: 'document-create',
      component: () => import('@/views/documents/DocumentDetailView.vue'),
      meta: { requiresWrite: true }
    },
    {
      path: '/documents/table',
      name: 'document-table',
      component: () => import('@/views/documents/TableView.vue')
    },
    {
      path: '/documents/:id',
      name: 'document-detail',
      component: () => import('@/views/documents/DocumentDetailView.vue'),
      props: true
    },
    // File routes
    {
      path: '/files',
      name: 'files',
      component: () => import('@/views/files/FileListView.vue')
    },
    {
      path: '/files/upload',
      name: 'file-upload',
      component: () => import('@/views/files/FileUploadView.vue'),
      meta: { requiresWrite: true }
    },
    {
      path: '/files/orphans',
      name: 'file-orphans',
      component: () => import('@/views/files/OrphanFilesView.vue')
    },
    {
      path: '/files/:id',
      name: 'file-detail',
      component: () => import('@/views/files/FileDetailView.vue'),
      props: true
    },
    // Audit Trail routes
    {
      path: '/audit',
      name: 'audit-overview',
      component: () => import('@/views/audit/AuditOverviewView.vue')
    },
    {
      path: '/audit/explorer',
      name: 'audit-explorer',
      component: () => import('@/views/audit/AuditExplorerView.vue')
    },
    // Admin routes
    {
      path: '/namespaces',
      name: 'namespaces',
      component: () => import('@/views/NamespacesView.vue')
    },
    {
      path: '/registry',
      name: 'registry',
      component: () => import('@/views/registry/RegistryListView.vue')
    },
    {
      path: '/registry/:id',
      name: 'registry-detail',
      component: () => import('@/views/registry/RegistryDetailView.vue'),
      props: true
    }
  ]
})

// Route guard: block write routes for read-only users
router.beforeEach((to) => {
  if (to.meta.requiresWrite || to.meta.requiresAdmin) {
    const ns = useNamespaceStore()
    if (to.meta.requiresAdmin && !ns.isAdmin) {
      return { name: 'home' }
    }
    if (to.meta.requiresWrite && !ns.canWrite) {
      return { name: 'home' }
    }
  }
})

export default router
