<script setup lang="ts">
import { watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import Toast from 'primevue/toast'
import ConfirmDialog from 'primevue/confirmdialog'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useUiStore } from '@/stores'

const toast = useToast()
const uiStore = useUiStore()

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
  <AppLayout>
    <router-view />
  </AppLayout>
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
