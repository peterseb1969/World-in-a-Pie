import { createRouter, createWebHistory } from 'vue-router'

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
      component: () => import('@/views/terminologies/ImportView.vue')
    },
    {
      path: '/terminologies/validate',
      name: 'terminology-validate',
      component: () => import('@/views/terminologies/ValidateView.vue')
    },
    {
      path: '/terminologies/:id',
      name: 'terminology-detail',
      component: () => import('@/views/terminologies/TerminologyDetailView.vue'),
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
      component: () => import('@/views/templates/TemplateDetailView.vue')
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
      path: '/documents/new',
      name: 'document-create',
      component: () => import('@/views/documents/DocumentDetailView.vue')
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
      component: () => import('@/views/files/FileUploadView.vue')
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
      name: 'namespace-groups',
      component: () => import('@/views/NamespaceGroupsView.vue')
    }
  ]
})

export default router
