<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore, useUiStore } from '@/stores'
import ProgressSpinner from 'primevue/progressspinner'

const router = useRouter()
const authStore = useAuthStore()
const uiStore = useUiStore()

const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const user = await authStore.handleOidcCallback()
    uiStore.showSuccess('Login Successful', `Welcome, ${user.profile.name || user.profile.email}!`)
    router.replace('/')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Authentication failed'
    uiStore.showError('Login Failed', error.value)
    // Redirect to home after a delay
    setTimeout(() => {
      router.replace('/')
    }, 3000)
  }
})
</script>

<template>
  <div class="callback-container">
    <div class="callback-content">
      <template v-if="!error">
        <ProgressSpinner />
        <p>Completing login...</p>
      </template>
      <template v-else>
        <i class="pi pi-exclamation-triangle error-icon"></i>
        <p class="error-message">{{ error }}</p>
        <p class="redirect-message">Redirecting to home...</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.callback-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--p-surface-ground);
}

.callback-content {
  text-align: center;
  padding: 2rem;
}

.callback-content p {
  margin-top: 1rem;
  color: var(--p-text-muted-color);
}

.error-icon {
  font-size: 3rem;
  color: var(--p-red-500);
}

.error-message {
  color: var(--p-red-600);
  font-weight: 500;
}

.redirect-message {
  font-size: 0.875rem;
}
</style>
