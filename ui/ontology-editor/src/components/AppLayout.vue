<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import Menubar from 'primevue/menubar'
import Menu from 'primevue/menu'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import { useAuthStore, useUiStore } from '@/stores'

const router = useRouter()
const authStore = useAuthStore()
const uiStore = useUiStore()

const showApiKeyDialog = ref(false)
const apiKeyInput = ref('')

const menuItems = [
  {
    label: 'Dashboard',
    icon: 'pi pi-home',
    command: () => router.push('/')
  },
  {
    label: 'Terminologies',
    icon: 'pi pi-list',
    command: () => router.push('/terminologies')
  },
  {
    label: 'Import',
    icon: 'pi pi-upload',
    command: () => router.push('/import')
  },
  {
    label: 'Validate',
    icon: 'pi pi-check-circle',
    command: () => router.push('/validate')
  }
]

const userMenuItems = ref([
  {
    label: 'Set API Key',
    icon: 'pi pi-key',
    command: () => {
      apiKeyInput.value = authStore.apiKey
      showApiKeyDialog.value = true
    }
  },
  {
    label: 'Clear API Key',
    icon: 'pi pi-sign-out',
    command: () => {
      authStore.clearApiKey()
      uiStore.showInfo('API Key Cleared')
    }
  }
])

const userMenu = ref()

function toggleUserMenu(event: Event) {
  userMenu.value.toggle(event)
}

function saveApiKey() {
  authStore.setApiKey(apiKeyInput.value)
  showApiKeyDialog.value = false
  uiStore.showSuccess('API Key Saved', 'Your API key has been stored locally')
}
</script>

<template>
  <div class="app-layout">
    <header class="app-header">
      <Menubar :model="menuItems" class="app-menubar">
        <template #start>
          <div class="app-brand" @click="router.push('/')">
            <i class="pi pi-sitemap" style="font-size: 1.5rem"></i>
            <span class="brand-text">Ontology Editor</span>
          </div>
        </template>
        <template #end>
          <div class="header-actions">
            <span v-if="authStore.isAuthenticated" class="auth-indicator">
              <i class="pi pi-check-circle" style="color: var(--p-green-500)"></i>
              Authenticated
            </span>
            <span v-else class="auth-indicator">
              <i class="pi pi-exclamation-circle" style="color: var(--p-orange-500)"></i>
              No API Key
            </span>
            <Button
              icon="pi pi-user"
              severity="secondary"
              text
              rounded
              @click="toggleUserMenu"
            />
            <Menu ref="userMenu" :model="userMenuItems" popup />
          </div>
        </template>
      </Menubar>
    </header>

    <main class="app-main">
      <slot />
    </main>

    <Dialog
      v-model:visible="showApiKeyDialog"
      header="Set API Key"
      :style="{ width: '400px' }"
      modal
    >
      <div class="api-key-form">
        <label for="apiKey">API Key</label>
        <InputText
          id="apiKey"
          v-model="apiKeyInput"
          type="password"
          placeholder="Enter your API key"
          class="w-full"
        />
        <small class="text-muted">
          For development, use: dev_master_key_for_testing
        </small>
      </div>
      <template #footer>
        <Button
          label="Cancel"
          severity="secondary"
          text
          @click="showApiKeyDialog = false"
        />
        <Button label="Save" @click="saveApiKey" />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  position: sticky;
  top: 0;
  z-index: 100;
}

.app-menubar {
  border-radius: 0;
  border-left: none;
  border-right: none;
  border-top: none;
}

.app-brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  margin-right: 1rem;
  padding-right: 1rem;
  border-right: 1px solid var(--p-surface-border);
}

.brand-text {
  font-weight: 600;
  font-size: 1.1rem;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.auth-indicator {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.app-main {
  flex: 1;
  padding: 1.5rem;
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
}

.api-key-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.api-key-form label {
  font-weight: 500;
}

.w-full {
  width: 100%;
}

.text-muted {
  color: var(--p-text-muted-color);
}
</style>
