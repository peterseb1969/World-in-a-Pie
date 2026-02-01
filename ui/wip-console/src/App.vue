<script setup lang="ts">
import { watch, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'primevue/usetoast'
import Toast from 'primevue/toast'
import ConfirmDialog from 'primevue/confirmdialog'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useUiStore, useAuthStore } from '@/stores'
import { setAuthErrorHandler } from '@/api/client'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const uiStore = useUiStore()
const authStore = useAuthStore()

// Check if current route should skip layout (auth callbacks)
const showLayout = computed(() => {
  return route.meta?.layout !== 'none'
})

// Initialize auth store on mount
onMounted(async () => {
  await authStore.initialize()

  // Set up auth error handler for API client
  // When a 401/403 is received, clear auth and redirect to home
  setAuthErrorHandler(() => {
    authStore.logout()
    router.push('/')
  })
})

// Watch for toast messages and display them
watch(
  () => uiStore.toastMessages.length,
  () => {
    let message = uiStore.consumeToast()
    while (message) {
      toast.add(message)
      message = uiStore.consumeToast()
    }
  }
)
</script>

<template>
  <Toast position="top-right" />
  <ConfirmDialog />

  <!-- Routes with layout: 'none' render without AppLayout (e.g., auth callbacks) -->
  <template v-if="showLayout">
    <AppLayout>
      <router-view />
    </AppLayout>
  </template>
  <template v-else>
    <router-view />
  </template>
</template>

<style>
:root {
  --font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
  --sidebar-width: 260px;
  --sidebar-collapsed-width: 60px;
  --header-height: 56px;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: var(--font-family);
  background-color: var(--p-surface-ground);
  color: var(--p-text-color);
  min-height: 100vh;
}

#app {
  min-height: 100vh;
}
</style>
