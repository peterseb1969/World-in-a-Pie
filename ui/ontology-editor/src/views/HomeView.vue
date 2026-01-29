<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import { useTerminologyStore, useAuthStore, useUiStore } from '@/stores'

const router = useRouter()
const terminologyStore = useTerminologyStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

const loading = ref(true)

const stats = computed(() => ({
  terminologies: terminologyStore.total,
  active: terminologyStore.terminologies.filter(t => t.status === 'active').length,
  deprecated: terminologyStore.terminologies.filter(t => t.status === 'deprecated').length,
  totalTerms: terminologyStore.terminologies.reduce((sum, t) => sum + t.term_count, 0)
}))

onMounted(async () => {
  if (!authStore.isAuthenticated) {
    uiStore.showWarn('No API Key', 'Set an API key to interact with the Def-Store')
  }
  try {
    await terminologyStore.fetchTerminologies()
  } catch (e) {
    // Error already shown by store
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="home-view">
    <div class="welcome-section">
      <h1>Ontology Editor</h1>
      <p class="subtitle">Manage terminologies and terms for World In a Pie</p>
    </div>

    <div class="stats-grid">
      <Card class="stat-card">
        <template #content>
          <div class="stat-content">
            <i class="pi pi-list stat-icon"></i>
            <div class="stat-info">
              <span class="stat-value">{{ stats.terminologies }}</span>
              <span class="stat-label">Terminologies</span>
            </div>
          </div>
        </template>
      </Card>

      <Card class="stat-card">
        <template #content>
          <div class="stat-content">
            <i class="pi pi-check-circle stat-icon success"></i>
            <div class="stat-info">
              <span class="stat-value">{{ stats.active }}</span>
              <span class="stat-label">Active</span>
            </div>
          </div>
        </template>
      </Card>

      <Card class="stat-card">
        <template #content>
          <div class="stat-content">
            <i class="pi pi-tag stat-icon info"></i>
            <div class="stat-info">
              <span class="stat-value">{{ stats.totalTerms }}</span>
              <span class="stat-label">Total Terms</span>
            </div>
          </div>
        </template>
      </Card>

      <Card class="stat-card">
        <template #content>
          <div class="stat-content">
            <i class="pi pi-exclamation-triangle stat-icon warn"></i>
            <div class="stat-info">
              <span class="stat-value">{{ stats.deprecated }}</span>
              <span class="stat-label">Deprecated</span>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <div class="quick-actions">
      <h2>Quick Actions</h2>
      <div class="action-cards">
        <Card class="action-card" @click="router.push('/terminologies')">
          <template #content>
            <div class="action-content">
              <i class="pi pi-list"></i>
              <div>
                <h3>Browse Terminologies</h3>
                <p>View and manage all terminologies</p>
              </div>
            </div>
          </template>
        </Card>

        <Card class="action-card" @click="router.push('/import')">
          <template #content>
            <div class="action-content">
              <i class="pi pi-upload"></i>
              <div>
                <h3>Import Data</h3>
                <p>Import terminologies from JSON or CSV</p>
              </div>
            </div>
          </template>
        </Card>

        <Card class="action-card" @click="router.push('/validate')">
          <template #content>
            <div class="action-content">
              <i class="pi pi-check-circle"></i>
              <div>
                <h3>Validate Values</h3>
                <p>Check values against terminologies</p>
              </div>
            </div>
          </template>
        </Card>
      </div>
    </div>

    <div v-if="terminologyStore.terminologies.length > 0" class="recent-section">
      <h2>Recent Terminologies</h2>
      <div class="recent-list">
        <Card
          v-for="term in terminologyStore.terminologies.slice(0, 5)"
          :key="term.terminology_id"
          class="recent-card"
          @click="router.push(`/terminologies/${term.terminology_id}`)"
        >
          <template #content>
            <div class="recent-content">
              <div class="recent-info">
                <span class="code-badge">{{ term.code }}</span>
                <span class="recent-name">{{ term.name }}</span>
              </div>
              <span class="term-count">{{ term.term_count }} terms</span>
            </div>
          </template>
        </Card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-view {
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.welcome-section {
  text-align: center;
  padding: 2rem 0;
}

.welcome-section h1 {
  margin: 0 0 0.5rem 0;
  font-size: 2.5rem;
}

.subtitle {
  color: var(--p-text-muted-color);
  font-size: 1.1rem;
  margin: 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
}

.stat-card {
  cursor: default;
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.stat-icon {
  font-size: 2rem;
  color: var(--p-primary-color);
}

.stat-icon.success {
  color: var(--p-green-500);
}

.stat-icon.warn {
  color: var(--p-orange-500);
}

.stat-icon.info {
  color: var(--p-blue-500);
}

.stat-info {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 1.75rem;
  font-weight: 600;
}

.stat-label {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.quick-actions h2,
.recent-section h2 {
  margin: 0 0 1rem 0;
  font-size: 1.25rem;
}

.action-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1rem;
}

.action-card {
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}

.action-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.action-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.action-content i {
  font-size: 1.5rem;
  color: var(--p-primary-color);
}

.action-content h3 {
  margin: 0 0 0.25rem 0;
  font-size: 1rem;
}

.action-content p {
  margin: 0;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.recent-card {
  cursor: pointer;
  transition: background-color 0.15s;
}

.recent-card:hover {
  background-color: var(--p-surface-hover);
}

.recent-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.recent-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
}

.recent-name {
  font-weight: 500;
}

.term-count {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}
</style>
