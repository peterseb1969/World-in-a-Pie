<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Divider from 'primevue/divider'
import { useAuthStore, useUiStore, useNamespaceStore } from '@/stores'
import { oidcProviderName, isOidcEnabled } from '@/config/auth'
import { isFilesEnabled, isReportingEnabled } from '@/config/modules'
import NamespaceSelector from './NamespaceSelector.vue'

const oidcEnabled = isOidcEnabled()
const namespaceStore = useNamespaceStore()

// Load namespaces on mount (if authenticated)
onMounted(() => {
  if (authStore.isAuthenticated) {
    namespaceStore.loadNamespaces()
  }
})

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const uiStore = useUiStore()

const showAuthDialog = ref(false)
const apiKeyInput = ref('')
const showApiKeyForm = ref(false)
const showOidcForm = ref(false)
const oidcUsername = ref('')
const oidcPassword = ref('')

const sidebarCollapsed = ref(false)

interface MenuItem {
  label: string
  icon: string
  route?: string
  children?: MenuItem[]
}

interface MenuSection {
  header?: string
  items: MenuItem[]
}

const filesEnabled = isFilesEnabled()
const reportingEnabled = isReportingEnabled()

const menuSections = computed<MenuSection[]>(() => {
  const dataItems: MenuItem[] = [
    {
      label: 'Terminologies',
      icon: 'pi pi-book',
      children: [
        { label: 'Browse', icon: 'pi pi-list', route: '/terminologies' },
        { label: 'New Terminology', icon: 'pi pi-plus', route: '/terminologies?create=true' },
        { label: 'Import', icon: 'pi pi-upload', route: '/terminologies/import' },
        { label: 'Validate', icon: 'pi pi-check-circle', route: '/terminologies/validate' },
        { label: 'Ontology Browser', icon: 'pi pi-sitemap', route: '/terminologies?tab=ontology' }
      ]
    },
    {
      label: 'Templates',
      icon: 'pi pi-file',
      children: [
        { label: 'Browse', icon: 'pi pi-list', route: '/templates' },
        { label: 'New Template', icon: 'pi pi-plus', route: '/templates/new' }
      ]
    },
    {
      label: 'Documents',
      icon: 'pi pi-folder',
      children: [
        { label: 'Browse', icon: 'pi pi-list', route: '/documents' },
        { label: 'Table View', icon: 'pi pi-table', route: '/documents/table' },
        { label: 'New Document', icon: 'pi pi-plus', route: '/documents/new' }
      ]
    },
  ]

  if (filesEnabled) {
    dataItems.push({
      label: 'Files',
      icon: 'pi pi-images',
      children: [
        { label: 'Browse', icon: 'pi pi-list', route: '/files' },
        { label: 'Orphans', icon: 'pi pi-exclamation-triangle', route: '/files/orphans' },
        { label: 'Upload', icon: 'pi pi-upload', route: '/files/upload' }
      ]
    })
  }

  const adminItems: MenuItem[] = [
    { label: 'Namespaces', icon: 'pi pi-database', route: '/namespaces' },
    { label: 'Registry', icon: 'pi pi-id-card', route: '/registry' },
  ]

  if (reportingEnabled) {
    adminItems.push({
      label: 'Audit Trail',
      icon: 'pi pi-history',
      children: [
        { label: 'Overview', icon: 'pi pi-chart-bar', route: '/audit' },
        { label: 'Explorer', icon: 'pi pi-search', route: '/audit/explorer' }
      ]
    })
  }

  return [
    { items: [{ label: 'Dashboard', icon: 'pi pi-home', route: '/' }] },
    { items: dataItems },
    { items: adminItems },
  ]
})

const expandedMenus = ref<Record<string, boolean>>({
  'Terminologies': true,
  'Templates': true,
  'Documents': true,
  ...(filesEnabled ? { 'Files': true } : {}),
  ...(reportingEnabled ? { 'Audit Trail': true } : {}),
})

function toggleMenu(label: string) {
  expandedMenus.value[label] = !expandedMenus.value[label]
}

function isActiveRoute(routePath: string | undefined): boolean {
  if (!routePath) return false
  if (routePath === '/') {
    return route.path === '/'
  }
  return route.path.startsWith(routePath)
}

function navigate(routePath: string | undefined) {
  if (routePath) {
    router.push(routePath)
  }
}

function openAuthDialog() {
  apiKeyInput.value = authStore.apiKey
  showApiKeyForm.value = false
  showAuthDialog.value = true
}

function closeAuthDialog() {
  showAuthDialog.value = false
  showApiKeyForm.value = false
  showOidcForm.value = false
  apiKeyInput.value = ''
  oidcUsername.value = ''
  oidcPassword.value = ''
}

async function loginWithOidcPassword() {
  if (!oidcUsername.value.trim() || !oidcPassword.value) {
    uiStore.showError('Login Failed', 'Please enter username and password')
    return
  }
  try {
    await authStore.loginWithPassword(oidcUsername.value.trim(), oidcPassword.value)
    uiStore.showSuccess('Login Successful', `Welcome, ${authStore.currentUser?.name || authStore.currentUser?.email || 'User'}`)
    closeAuthDialog()
    // Refresh dashboard data by navigating to home
    router.push('/')
  } catch (err) {
    uiStore.showError('Login Failed', err instanceof Error ? err.message : 'Failed to login')
  }
}

function saveApiKey() {
  if (apiKeyInput.value.trim()) {
    authStore.setApiKey(apiKeyInput.value.trim())
    uiStore.showSuccess('API Key Saved', 'Your API key has been saved successfully')
    closeAuthDialog()
    // Refresh dashboard data by navigating to home
    router.push('/')
  } else {
    closeAuthDialog()
  }
}

async function logout() {
  try {
    await authStore.logout()
    uiStore.showInfo('Logged Out', 'You have been logged out successfully')
    closeAuthDialog()
    // Redirect to dashboard after logout
    router.push('/')
  } catch (err) {
    console.error('Logout error:', err)
  }
}

const authStatusText = computed(() => {
  if (authStore.authMode === 'oidc' && authStore.currentUser) {
    return authStore.currentUser.name || authStore.currentUser.email
  }
  if (authStore.authMode === 'api_key') {
    return 'API Key'
  }
  return 'Not Connected'
})

const authStatusClass = computed(() => {
  return authStore.isAuthenticated ? 'status-connected' : 'status-disconnected'
})

const authIcon = computed(() => {
  if (authStore.authMode === 'oidc') {
    return 'pi pi-user'
  }
  return 'pi pi-key'
})

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}
</script>

<template>
  <div class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-header">
        <div class="logo" v-if="!sidebarCollapsed">
          <img src="@/assets/logo.png" alt="WIP" class="logo-image" />
        </div>
        <Button
          :icon="sidebarCollapsed ? 'pi pi-angle-right' : 'pi pi-angle-left'"
          text
          rounded
          size="small"
          @click="toggleSidebar"
          class="toggle-btn"
        />
      </div>

      <nav class="sidebar-nav">
        <template v-for="section in menuSections" :key="section.header || 'main'">
          <div v-if="section.header && !sidebarCollapsed" class="section-header">
            {{ section.header }}
          </div>
          <div v-else-if="section.header && sidebarCollapsed" class="section-divider"></div>
          <ul class="menu-list">
            <template v-for="item in section.items" :key="item.label">
              <!-- Simple menu item -->
              <li v-if="!item.children" class="menu-item">
                <a
                  class="menu-link"
                  :class="{ active: isActiveRoute(item.route) }"
                  @click="navigate(item.route)"
                >
                  <i :class="item.icon"></i>
                  <span v-if="!sidebarCollapsed" class="menu-label">{{ item.label }}</span>
                </a>
              </li>

              <!-- Menu item with children -->
              <li v-else class="menu-item has-children">
                <a
                  class="menu-link parent"
                  :class="{ expanded: expandedMenus[item.label] }"
                  @click="toggleMenu(item.label)"
                >
                  <i :class="item.icon"></i>
                  <span v-if="!sidebarCollapsed" class="menu-label">{{ item.label }}</span>
                  <i
                    v-if="!sidebarCollapsed"
                    class="pi expand-icon"
                    :class="expandedMenus[item.label] ? 'pi-chevron-down' : 'pi-chevron-right'"
                  ></i>
                </a>
                <ul v-if="!sidebarCollapsed && expandedMenus[item.label]" class="submenu">
                  <li v-for="child in item.children" :key="child.label" class="menu-item">
                    <a
                      class="menu-link"
                      :class="{ active: isActiveRoute(child.route) }"
                      @click="navigate(child.route)"
                    >
                      <i :class="child.icon"></i>
                      <span class="menu-label">{{ child.label }}</span>
                    </a>
                  </li>
                </ul>
              </li>
            </template>
          </ul>
        </template>
      </nav>

      <!-- Sidebar footer -->
      <div class="sidebar-footer" v-if="!sidebarCollapsed">
        <div class="auth-status" :class="authStatusClass" @click="openAuthDialog">
          <i :class="authIcon"></i>
          <span>{{ authStatusText }}</span>
        </div>
      </div>
      <div class="sidebar-footer collapsed-footer" v-else>
        <Button
          :icon="authIcon"
          text
          rounded
          :severity="authStore.isAuthenticated ? 'success' : 'danger'"
          @click="openAuthDialog"
        />
      </div>
    </aside>

    <!-- Main content -->
    <main class="main-content" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
      <header class="main-header">
        <div class="header-title">
          <NamespaceSelector />
        </div>
        <div class="header-actions">
          <Button
            icon="pi pi-cog"
            text
            rounded
            @click="openAuthDialog"
            v-tooltip.left="'Settings'"
          />
        </div>
      </header>
      <div class="content-area">
        <!-- No access gate -->
        <div v-if="authStore.isAuthenticated && namespaceStore.noAccess" class="no-access-overlay">
          <div class="no-access-card">
            <i class="pi pi-lock no-access-icon"></i>
            <h2>No Namespace Access</h2>
            <p>Your account does not have access to any namespaces.</p>
            <p class="no-access-help">Contact an administrator to request access.</p>
            <Button
              label="Logout"
              severity="secondary"
              icon="pi pi-sign-out"
              @click="logout"
            />
          </div>
        </div>
        <slot v-else />
      </div>
    </main>

    <!-- Auth Dialog -->
    <Dialog
      v-model:visible="showAuthDialog"
      :header="authStore.isAuthenticated ? 'Account' : 'Login'"
      :modal="true"
      :style="{ width: '450px' }"
    >
      <!-- Logged in state -->
      <div v-if="authStore.isAuthenticated" class="auth-content">
        <!-- OIDC user info -->
        <div v-if="authStore.authMode === 'oidc' && authStore.currentUser" class="user-info">
          <div class="user-avatar">
            <i class="pi pi-user"></i>
          </div>
          <div class="user-details">
            <div class="user-name">{{ authStore.currentUser.name }}</div>
            <div class="user-email">{{ authStore.currentUser.email }}</div>
          </div>
        </div>

        <!-- API Key info -->
        <div v-else-if="authStore.authMode === 'api_key'" class="api-key-info">
          <i class="pi pi-key"></i>
          <span>Authenticated with API Key</span>
        </div>
      </div>

      <!-- Not logged in state -->
      <div v-else class="auth-content">
        <!-- API Key form -->
        <div v-if="showApiKeyForm" class="api-key-form">
          <p class="help-text">
            Enter your API key to authenticate with WIP services.
            For development, use: <code>dev_master_key_for_testing</code>
          </p>
          <div class="input-group">
            <label for="api-key">API Key</label>
            <InputText
              id="api-key"
              v-model="apiKeyInput"
              type="password"
              placeholder="Enter your API key"
              class="w-full"
              @keyup.enter="saveApiKey"
            />
          </div>
          <div class="form-actions">
            <Button
              label="Back"
              severity="secondary"
              text
              @click="showApiKeyForm = false"
            />
            <Button
              label="Save"
              @click="saveApiKey"
              :disabled="!apiKeyInput.trim()"
            />
          </div>
        </div>

        <!-- OIDC Login form -->
        <div v-else-if="showOidcForm" class="oidc-form">
          <p class="help-text">
            Enter your credentials to login via {{ oidcProviderName }}.
            <br><small>Default: admin@wip.local / admin123</small>
          </p>
          <div class="input-group">
            <label for="oidc-username">Email / Username</label>
            <InputText
              id="oidc-username"
              v-model="oidcUsername"
              type="text"
              placeholder="admin@wip.local"
              class="w-full"
              @keyup.enter="loginWithOidcPassword"
            />
          </div>
          <div class="input-group">
            <label for="oidc-password">Password</label>
            <InputText
              id="oidc-password"
              v-model="oidcPassword"
              type="password"
              placeholder="Password"
              class="w-full"
              @keyup.enter="loginWithOidcPassword"
            />
          </div>
          <div class="form-actions">
            <Button
              label="Back"
              severity="secondary"
              text
              @click="showOidcForm = false"
            />
            <Button
              label="Login"
              @click="loginWithOidcPassword"
              :loading="authStore.isLoading"
              :disabled="!oidcUsername.trim() || !oidcPassword"
            />
          </div>
        </div>

        <!-- Login options -->
        <div v-else class="login-options">
          <p class="help-text">
            <template v-if="oidcEnabled">
              Choose how you want to authenticate with WIP Console.
            </template>
            <template v-else>
              Enter your API key to authenticate with WIP Console.
              <br><small>For development, use: <code>dev_master_key_for_testing</code></small>
            </template>
          </p>

          <template v-if="oidcEnabled">
            <Button
              :label="`Login with ${oidcProviderName}`"
              icon="pi pi-sign-in"
              class="w-full login-btn"
              @click="authStore.loginWithOidc()"
            />

            <Divider align="center">
              <span class="divider-text">or</span>
            </Divider>
          </template>

          <Button
            label="Use API Key"
            icon="pi pi-key"
            :severity="oidcEnabled ? 'secondary' : undefined"
            :outlined="oidcEnabled"
            class="w-full"
            :class="{ 'login-btn': !oidcEnabled }"
            @click="showApiKeyForm = true"
          />
        </div>
      </div>

      <template #footer>
        <div v-if="authStore.isAuthenticated" class="dialog-footer">
          <Button
            label="Logout"
            severity="danger"
            text
            @click="logout"
          />
          <Button
            label="Close"
            severity="secondary"
            text
            @click="closeAuthDialog"
          />
        </div>
        <div v-else-if="!showApiKeyForm && !showOidcForm" class="dialog-footer">
          <Button
            label="Cancel"
            severity="secondary"
            text
            @click="closeAuthDialog"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}

/* Sidebar */
.sidebar {
  width: var(--sidebar-width);
  background-color: var(--p-surface-0);
  border-right: 1px solid var(--p-surface-200);
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  z-index: 100;
  transition: width 0.2s ease;
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed-width);
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--p-surface-200);
  min-height: var(--header-height);
}

.logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.logo-image {
  height: 36px;
  width: auto;
}

.toggle-btn {
  flex-shrink: 0;
}

.sidebar.collapsed .sidebar-header {
  justify-content: center;
  padding: 1rem 0.5rem;
}

/* Navigation */
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}

.section-header {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--p-text-muted-color);
  padding: 0.75rem 1rem 0.25rem;
}

.section-divider {
  height: 1px;
  background: var(--p-surface-200);
  margin: 0.5rem 0.75rem;
}

.menu-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.menu-item {
  margin: 0.125rem 0;
}

.menu-link {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  color: var(--p-text-color);
  text-decoration: none;
  cursor: pointer;
  border-radius: 0;
  transition: background-color 0.15s, color 0.15s;
  margin: 0 0.5rem;
  border-radius: 6px;
}

.menu-link:hover {
  background-color: var(--p-surface-100);
}

.menu-link.active {
  background-color: var(--p-primary-50);
  color: var(--p-primary-color);
  font-weight: 500;
}

.menu-link i {
  font-size: 1rem;
  width: 1.25rem;
  text-align: center;
}

.menu-label {
  flex: 1;
}

.expand-icon {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.menu-link.parent {
  font-weight: 500;
  text-transform: uppercase;
  font-size: 0.75rem;
  letter-spacing: 0.05em;
  color: var(--p-text-muted-color);
  margin-top: 1rem;
}

.menu-link.parent:hover {
  background-color: transparent;
  color: var(--p-text-color);
}

.submenu {
  list-style: none;
  padding: 0;
  margin: 0;
}

.submenu .menu-link {
  padding-left: 2.5rem;
  font-size: 0.875rem;
}

/* Sidebar footer */
.sidebar-footer {
  padding: 1rem;
  border-top: 1px solid var(--p-surface-200);
}

.collapsed-footer {
  display: flex;
  justify-content: center;
  padding: 0.5rem;
}

.auth-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.875rem;
  cursor: pointer;
  transition: background-color 0.15s;
}

.auth-status:hover {
  background-color: var(--p-surface-100);
}

.auth-status.status-connected {
  color: var(--p-green-600);
}

.auth-status.status-disconnected {
  color: var(--p-red-600);
}

/* Main content */
.main-content {
  flex: 1;
  margin-left: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  transition: margin-left 0.2s ease;
}

.main-content.sidebar-collapsed {
  margin-left: var(--sidebar-collapsed-width);
}

.main-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.5rem;
  height: var(--header-height);
  background-color: var(--p-surface-0);
  border-bottom: 1px solid var(--p-surface-200);
  position: sticky;
  top: 0;
  z-index: 50;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.content-area {
  flex: 1;
  padding: 1.5rem;
  background-color: var(--p-surface-ground);
}

/* Auth Dialog */
.auth-content {
  padding: 0.5rem 0;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  background-color: var(--p-surface-50);
  border-radius: 8px;
}

.user-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background-color: var(--p-primary-100);
  display: flex;
  align-items: center;
  justify-content: center;
}

.user-avatar i {
  font-size: 1.5rem;
  color: var(--p-primary-600);
}

.user-details {
  flex: 1;
}

.user-name {
  font-weight: 600;
  font-size: 1rem;
}

.user-email {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.api-key-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  background-color: var(--p-surface-50);
  border-radius: 8px;
  color: var(--p-text-muted-color);
}

.api-key-info i {
  font-size: 1.25rem;
}

.login-options {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.login-btn {
  height: 48px;
  font-size: 1rem;
}

.help-text {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  line-height: 1.5;
  margin-bottom: 1rem;
}

.help-text code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.8125rem;
}

.divider-text {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.api-key-form,
.oidc-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.input-group label {
  font-size: 0.875rem;
  font-weight: 500;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.dialog-footer {
  display: flex;
  justify-content: space-between;
  width: 100%;
}

.w-full {
  width: 100%;
}

/* No Access Overlay */
.no-access-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}

.no-access-card {
  text-align: center;
  padding: 3rem;
  background-color: var(--p-surface-0);
  border-radius: 12px;
  border: 1px solid var(--p-surface-200);
  max-width: 400px;
}

.no-access-icon {
  font-size: 3rem;
  color: var(--p-text-muted-color);
  margin-bottom: 1rem;
}

.no-access-card h2 {
  margin: 0 0 0.5rem;
  color: var(--p-text-color);
}

.no-access-card p {
  color: var(--p-text-muted-color);
  margin: 0 0 0.5rem;
}

.no-access-help {
  margin-bottom: 1.5rem !important;
  font-size: 0.875rem;
}

/* Responsive */
@media (max-width: 768px) {
  .sidebar {
    width: var(--sidebar-collapsed-width);
  }

  .sidebar .logo,
  .sidebar .menu-label,
  .sidebar .expand-icon,
  .sidebar .auth-status span,
  .sidebar .submenu {
    display: none;
  }

  .main-content {
    margin-left: var(--sidebar-collapsed-width);
  }
}
</style>
