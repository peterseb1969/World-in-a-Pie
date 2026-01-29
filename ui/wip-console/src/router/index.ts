import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue')
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
    }
  ]
})

export default router
