<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { useAuthStore, useTerminologyStore, useTemplateStore, useUiStore } from '@/stores'
import { reportingSyncClient, type IntegrityCheckResult, type IntegrityIssue } from '@/api/client'
import type { Terminology, Template } from '@/types'

const router = useRouter()
const authStore = useAuthStore()
const terminologyStore = useTerminologyStore()
const templateStore = useTemplateStore()
const uiStore = useUiStore()

const loading = ref(true)

// Stats
const terminologyStats = ref({
  total: 0,
  active: 0,
  deprecated: 0,
  inactive: 0
})

const templateStats = ref({
  total: 0,
  active: 0,
  deprecated: 0,
  inactive: 0
})

// Recent items
const recentTerminologies = ref<Terminology[]>([])
const recentTemplates = ref<Template[]>([])

// Data quality / integrity check
const integrityLoading = ref(false)
const integrityResult = ref<IntegrityCheckResult | null>(null)
const integrityError = ref<string | null>(null)

async function loadDashboard() {
  if (!authStore.isAuthenticated) {
    loading.value = false
    return
  }

  loading.value = true
  try {
    // Fetch terminologies
    await terminologyStore.fetchTerminologies({ page_size: 100 })
    const terms = terminologyStore.terminologies
    terminologyStats.value = {
      total: terms.length,
      active: terms.filter(t => t.status === 'active').length,
      deprecated: terms.filter(t => t.status === 'deprecated').length,
      inactive: terms.filter(t => t.status === 'inactive').length
    }
    // Get 5 most recent
    recentTerminologies.value = [...terms]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

    // Fetch templates
    await templateStore.fetchTemplates({ page_size: 100 })
    const templates = templateStore.templates
    templateStats.value = {
      total: templates.length,
      active: templates.filter(t => t.status === 'active').length,
      deprecated: templates.filter(t => t.status === 'deprecated').length,
      inactive: templates.filter(t => t.status === 'inactive').length
    }
    // Get 5 most recent
    recentTemplates.value = [...templates]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

  } catch (error) {
    uiStore.showError('Failed to load dashboard', error instanceof Error ? error.message : 'Unknown error')
  } finally {
    loading.value = false
  }

  // Load integrity check separately (don't block dashboard)
  loadIntegrityCheck()
}

async function loadIntegrityCheck() {
  if (!authStore.isAuthenticated) {
    return
  }

  integrityLoading.value = true
  integrityError.value = null

  try {
    integrityResult.value = await reportingSyncClient.getIntegrityCheck({
      template_limit: 500,
      document_limit: 500,
      check_term_refs: true
    })
  } catch (error) {
    console.error('Failed to load integrity check:', error)
    integrityError.value = error instanceof Error ? error.message : 'Unknown error'
    integrityResult.value = null
  } finally {
    integrityLoading.value = false
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  })
}

function navigateToTerminology(terminology: Terminology) {
  router.push(`/terminologies/${terminology.terminology_id}`)
}

function navigateToTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function getIntegritySeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'healthy': return 'success'
    case 'warning': return 'warn'
    case 'error': return 'danger'
    case 'partial': return 'secondary'
    default: return 'secondary'
  }
}

function getIssueSeverity(severity: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (severity) {
    case 'error': return 'danger'
    case 'warning': return 'warn'
    case 'info': return 'secondary'
    default: return 'secondary'
  }
}

function navigateToIssue(issue: IntegrityIssue) {
  if (issue.source === 'template-store') {
    router.push(`/templates/${issue.entity_id}`)
  } else if (issue.source === 'document-store') {
    router.push(`/documents/${issue.entity_id}`)
  }
}

onMounted(() => {
  loadDashboard()
})

// Watch for auth changes and reload dashboard when user logs in
watch(
  () => authStore.isAuthenticated,
  (isAuth, wasAuth) => {
    // Only reload when transitioning from not authenticated to authenticated
    if (isAuth && !wasAuth) {
      loadDashboard()
    }
  }
)
</script>

<template>
  <div class="home-view">
    <div class="page-header">
      <h1>Dashboard</h1>
      <p class="subtitle">Welcome to WIP Console - Manage your terminologies and templates</p>
    </div>

    <!-- Auth warning -->
    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <Card>
        <template #content>
          <div class="warning-content">
            <i class="pi pi-exclamation-triangle"></i>
            <div>
              <h3>API Key Required</h3>
              <p>Please configure your API key to access WIP services. Click the key icon in the sidebar footer.</p>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <template v-else>
      <!-- Statistics Cards -->
      <div class="stats-grid">
        <!-- Terminology Stats -->
        <Card class="stat-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-book"></i>
              <span>Terminologies</span>
            </div>
          </template>
          <template #content>
            <div class="stat-content">
              <div class="stat-number">{{ terminologyStats.total }}</div>
              <div class="stat-breakdown">
                <span class="stat-item active">
                  <span class="dot"></span>
                  {{ terminologyStats.active }} active
                </span>
                <span class="stat-item deprecated">
                  <span class="dot"></span>
                  {{ terminologyStats.deprecated }} deprecated
                </span>
              </div>
            </div>
          </template>
          <template #footer>
            <Button
              label="Browse Terminologies"
              icon="pi pi-arrow-right"
              iconPos="right"
              text
              @click="router.push('/terminologies')"
            />
          </template>
        </Card>

        <!-- Template Stats -->
        <Card class="stat-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-file"></i>
              <span>Templates</span>
            </div>
          </template>
          <template #content>
            <div class="stat-content">
              <div class="stat-number">{{ templateStats.total }}</div>
              <div class="stat-breakdown">
                <span class="stat-item active">
                  <span class="dot"></span>
                  {{ templateStats.active }} active
                </span>
                <span class="stat-item deprecated">
                  <span class="dot"></span>
                  {{ templateStats.deprecated }} deprecated
                </span>
              </div>
            </div>
          </template>
          <template #footer>
            <Button
              label="Browse Templates"
              icon="pi pi-arrow-right"
              iconPos="right"
              text
              @click="router.push('/templates')"
            />
          </template>
        </Card>

        <!-- Quick Actions -->
        <Card class="stat-card quick-actions">
          <template #title>
            <div class="card-title">
              <i class="pi pi-bolt"></i>
              <span>Quick Actions</span>
            </div>
          </template>
          <template #content>
            <div class="action-buttons">
              <Button
                label="Import Terminology"
                icon="pi pi-upload"
                severity="secondary"
                @click="router.push('/terminologies/import')"
              />
              <Button
                label="Validate Values"
                icon="pi pi-check-circle"
                severity="secondary"
                @click="router.push('/terminologies/validate')"
              />
              <Button
                label="New Template"
                icon="pi pi-plus"
                severity="secondary"
                @click="router.push('/templates/new')"
              />
            </div>
          </template>
        </Card>
      </div>

      <!-- Data Quality Section -->
      <div class="data-quality-section">
        <Card class="quality-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-shield"></i>
              <span>Data Quality</span>
              <Tag
                v-if="integrityResult"
                :severity="getIntegritySeverity(integrityResult.status)"
                class="status-tag"
              >
                {{ integrityResult.status }}
              </Tag>
            </div>
          </template>
          <template #content>
            <!-- Loading state -->
            <div v-if="integrityLoading" class="quality-loading">
              <ProgressSpinner style="width: 30px; height: 30px" />
              <span>Checking data integrity...</span>
            </div>

            <!-- Error state -->
            <Message v-else-if="integrityError" severity="warn" :closable="false">
              Could not check data integrity: {{ integrityError }}
            </Message>

            <!-- Results -->
            <div v-else-if="integrityResult" class="quality-content">
              <!-- Summary stats -->
              <div class="quality-summary">
                <div class="quality-stat">
                  <span class="stat-label">Templates Checked</span>
                  <span class="stat-value">{{ integrityResult.summary.total_templates }}</span>
                </div>
                <div class="quality-stat">
                  <span class="stat-label">Documents Checked</span>
                  <span class="stat-value">{{ integrityResult.summary.total_documents }}</span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityResult.summary.templates_with_issues > 0 }">
                  <span class="stat-label">Templates with Issues</span>
                  <span class="stat-value">{{ integrityResult.summary.templates_with_issues }}</span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityResult.summary.documents_with_issues > 0 }">
                  <span class="stat-label">Documents with Issues</span>
                  <span class="stat-value">{{ integrityResult.summary.documents_with_issues }}</span>
                </div>
              </div>

              <!-- Issue breakdown -->
              <div v-if="integrityResult.issues.length > 0" class="quality-issues">
                <h4>Issues Found ({{ integrityResult.issues.length }})</h4>
                <DataTable
                  :value="integrityResult.issues.slice(0, 10)"
                  size="small"
                  @row-click="(e) => navigateToIssue(e.data)"
                  class="clickable-rows"
                  :pt="{ bodyRow: { style: 'cursor: pointer' } }"
                >
                  <Column field="type" header="Type" style="width: 180px">
                    <template #body="{ data }">
                      <Tag :severity="getIssueSeverity(data.severity)" size="small">
                        {{ data.type.replace(/_/g, ' ') }}
                      </Tag>
                    </template>
                  </Column>
                  <Column field="entity_code" header="Entity" style="width: 120px">
                    <template #body="{ data }">
                      {{ data.entity_code || data.entity_id.substring(0, 12) }}
                    </template>
                  </Column>
                  <Column field="field_path" header="Field" style="width: 120px" />
                  <Column field="message" header="Message" />
                </DataTable>
                <div v-if="integrityResult.issues.length > 10" class="more-issues">
                  ... and {{ integrityResult.issues.length - 10 }} more issues
                </div>
              </div>

              <!-- All healthy -->
              <div v-else class="quality-healthy">
                <i class="pi pi-check-circle"></i>
                <span>All references are valid. No integrity issues found.</span>
              </div>

              <!-- Services status -->
              <div v-if="integrityResult.services_unavailable.length > 0" class="services-warning">
                <Message severity="warn" :closable="false">
                  Some services were unavailable: {{ integrityResult.services_unavailable.join(', ') }}
                </Message>
              </div>
            </div>
          </template>
          <template #footer>
            <Button
              label="Refresh"
              icon="pi pi-refresh"
              text
              size="small"
              :loading="integrityLoading"
              @click="loadIntegrityCheck"
            />
          </template>
        </Card>
      </div>

      <!-- Recent Items -->
      <div class="recent-grid">
        <!-- Recent Terminologies -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-clock"></i>
              <span>Recent Terminologies</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTerminologies"
              :loading="loading"
              @row-click="(e) => navigateToTerminology(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column field="code" header="Code" />
              <Column field="term_count" header="Terms" style="width: 80px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.term_count }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 100px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <Column field="updated_at" header="Updated" style="width: 120px">
                <template #body="{ data }">
                  {{ formatDate(data.updated_at) }}
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No terminologies found</div>
              </template>
            </DataTable>
          </template>
        </Card>

        <!-- Recent Templates -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-clock"></i>
              <span>Recent Templates</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTemplates"
              :loading="loading"
              @row-click="(e) => navigateToTemplate(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column field="code" header="Code" />
              <Column header="Fields" style="width: 80px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.fields?.length || 0 }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 100px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <Column field="updated_at" header="Updated" style="width: 120px">
                <template #body="{ data }">
                  {{ formatDate(data.updated_at) }}
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No templates found</div>
              </template>
            </DataTable>
          </template>
        </Card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.home-view {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 2rem;
}

.page-header h1 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.subtitle {
  color: var(--p-text-muted-color);
}

/* Auth warning */
.auth-warning {
  margin-bottom: 2rem;
}

.warning-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.warning-content i {
  font-size: 2rem;
  color: var(--p-orange-500);
}

.warning-content h3 {
  margin: 0 0 0.25rem 0;
  font-size: 1.125rem;
}

.warning-content p {
  margin: 0;
  color: var(--p-text-muted-color);
}

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}

.stat-card :deep(.p-card-title) {
  font-size: 1rem;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-title i {
  color: var(--p-primary-color);
}

.stat-content {
  text-align: center;
  padding: 1rem 0;
}

.stat-number {
  font-size: 3rem;
  font-weight: 600;
  color: var(--p-primary-color);
  line-height: 1;
  margin-bottom: 0.75rem;
}

.stat-breakdown {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.stat-item .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.stat-item.active .dot {
  background-color: var(--p-green-500);
}

.stat-item.deprecated .dot {
  background-color: var(--p-orange-500);
}

/* Quick actions */
.quick-actions .action-buttons {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.quick-actions .action-buttons :deep(.p-button) {
  justify-content: flex-start;
}

/* Recent grid */
.recent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
  gap: 1.5rem;
}

.recent-card :deep(.p-card-title) {
  font-size: 1rem;
}

.recent-card :deep(.p-card-content) {
  padding: 0;
}

.recent-card :deep(.p-datatable) {
  border: none;
}

.clickable-rows :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

/* Data Quality section */
.data-quality-section {
  margin-bottom: 2rem;
}

.quality-card :deep(.p-card-title) {
  font-size: 1rem;
}

.quality-card .status-tag {
  margin-left: auto;
}

.quality-loading {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
}

.quality-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.quality-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
}

.quality-stat {
  text-align: center;
  padding: 0.75rem;
  background: var(--p-surface-100);
  border-radius: 6px;
}

.quality-stat.has-issues {
  background: var(--p-red-50);
}

.quality-stat .stat-label {
  display: block;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin-bottom: 0.25rem;
}

.quality-stat .stat-value {
  display: block;
  font-size: 1.25rem;
  font-weight: 600;
}

.quality-stat.has-issues .stat-value {
  color: var(--p-red-500);
}

.quality-issues h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.quality-issues :deep(.p-datatable) {
  font-size: 0.875rem;
}

.more-issues {
  text-align: center;
  padding: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.quality-healthy {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  background: var(--p-green-50);
  border-radius: 6px;
  color: var(--p-green-700);
}

.quality-healthy i {
  font-size: 1.5rem;
}

.services-warning {
  margin-top: 0.5rem;
}

@media (max-width: 1024px) {
  .recent-grid {
    grid-template-columns: 1fr;
  }
}
</style>
