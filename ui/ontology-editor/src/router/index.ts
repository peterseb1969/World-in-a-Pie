import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue')
  },
  {
    path: '/terminologies',
    name: 'terminologies',
    component: () => import('@/views/TerminologyListView.vue')
  },
  {
    path: '/terminologies/:id',
    name: 'terminology-detail',
    component: () => import('@/views/TerminologyDetailView.vue'),
    props: true
  },
  {
    path: '/import',
    name: 'import',
    component: () => import('@/views/ImportView.vue')
  },
  {
    path: '/validate',
    name: 'validate',
    component: () => import('@/views/ValidateView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
