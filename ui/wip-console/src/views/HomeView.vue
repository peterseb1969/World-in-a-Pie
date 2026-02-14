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
import { useAuthStore, useTerminologyStore, useTemplateStore, useUiStore, useNamespaceStore } from '@/stores'
import { isReportingEnabled } from '@/config/modules'
import { reportingSyncClient, documentStoreClient, type IntegrityCheckResult } from '@/api/client'
import type { Terminology, Template, Document } from '@/types'

const router = useRouter()
const authStore = useAuthStore()
const terminologyStore = useTerminologyStore()
const templateStore = useTemplateStore()
const uiStore = useUiStore()
const namespaceStore = useNamespaceStore()

// Check if reporting module is enabled
const reportingEnabled = isReportingEnabled()

const loading = ref(true)

// Compact stats for all 5 entity types
const entityCounts = ref<Record<string, number>>({
  terminologies: 0,
  terms: 0,
  templates: 0,
  documents: 0,
  files: 0
})

// Recent items
const recentTerminologies = ref<Terminology[]>([])
const recentTemplates = ref<Template[]>([])
const recentDocuments = ref<Document[]>([])

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
    // Fetch terminologies — use API total as authoritative count
    await terminologyStore.fetchTerminologies({ page_size: 100 })
    const allTerms = terminologyStore.terminologies
    entityCounts.value.terminologies = terminologyStore.total
    // Sum term_count across all terminologies for total terms
    entityCounts.value.terms = allTerms.reduce((sum, t) => sum + (t.term_count || 0), 0)
    recentTerminologies.value = [...allTerms]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

    // Fetch templates — use API total as authoritative count
    await templateStore.fetchTemplates({ page_size: 100 })
    const templates = templateStore.templates
    entityCounts.value.templates = templateStore.total
    recentTemplates.value = [...templates]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

    // Fetch recent documents — use API total as authoritative count
    try {
      const docParams: Record<string, unknown> = { page_size: 5 }
      if (namespaceStore.documentsPool) {
        docParams.pool_id = namespaceStore.documentsPool
      }
      const docResponse = await documentStoreClient.listDocuments(docParams as any)
      recentDocuments.value = docResponse.items
      entityCounts.value.documents = docResponse.total
    } catch {
      // Document store may not be available
    }

  } catch (error) {
    uiStore.showError('Failed to load dashboard', error instanceof Error ? error.message : 'Unknown error')
  } finally {
    loading.value = false
  }

  // Load integrity check separately (don't block dashboard)
  if (reportingEnabled) {
    loadIntegrityCheck()
  }
}

async function loadIntegrityCheck() {
  if (!authStore.isAuthenticated) {
    return
  }

  integrityLoading.value = true
  integrityError.value = null

  try {
    integrityResult.value = await reportingSyncClient.getIntegrityCheck({
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
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function navigateToTerminology(terminology: Terminology) {
  router.push(`/terminologies/${terminology.terminology_id}`)
}

function navigateToTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function navigateToDocument(doc: Document) {
  router.push(`/documents/${doc.document_id}`)
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

const statItems = [
  { key: 'terminologies', label: 'Terminologies', icon: 'pi pi-book', route: '/terminologies' },
  { key: 'terms', label: 'Terms', icon: 'pi pi-tag', route: '/terminologies' },
  { key: 'templates', label: 'Templates', icon: 'pi pi-file', route: '/templates' },
  { key: 'documents', label: 'Documents', icon: 'pi pi-folder', route: '/documents' },
  { key: 'files', label: 'Files', icon: 'pi pi-paperclip', route: '/files' }
]

onMounted(() => {
  loadDashboard()
})

// Watch for auth changes and reload dashboard when user logs in
watch(
  () => authStore.isAuthenticated,
  (isAuth, wasAuth) => {
    if (isAuth && !wasAuth) {
      loadDashboard()
    }
  }
)

// Watch for namespace changes and reload dashboard
watch(() => namespaceStore.current, () => {
  loadDashboard()
})
</script>

<template>
  <div class="home-view">
    <div class="page-header">
      <h1>Dashboard</h1>
      <p class="subtitle">Namespace: <strong>{{ namespaceStore.isAll ? 'All' : namespaceStore.current }}</strong></p>
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
      <!-- Compact stat bar -->
      <div class="stat-bar">
        <div
          v-for="item in statItems"
          :key="item.key"
          class="stat-chip"
          @click="router.push(item.route)"
        >
          <i :class="item.icon"></i>
          <span class="stat-chip-count">{{ entityCounts[item.key] }}</span>
          <span class="stat-chip-label">{{ item.label }}</span>
        </div>
      </div>

      <!-- Quick Actions Row -->
      <div class="quick-actions-row">
        <Button
          label="New Terminology"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies?create=true')"
        />
        <Button
          label="Import Terminology"
          icon="pi pi-upload"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies/import')"
        />
        <Button
          label="New Template"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/templates/new')"
        />
        <Button
          label="New Document"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/documents/new')"
        />
        <Button
          label="Upload File"
          icon="pi pi-cloud-upload"
          severity="secondary"
          size="small"
          @click="router.push('/files/upload')"
        />
        <Button
          label="Validate Values"
          icon="pi pi-check-circle"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies/validate')"
        />
      </div>

      <!-- Data Quality Section (only shown when reporting module is enabled) -->
      <div v-if="reportingEnabled" class="data-quality-section">
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
            <div v-if="integrityLoading" class="quality-loading">
              <ProgressSpinner style="width: 30px; height: 30px" />
              <span>Checking data integrity...</span>
            </div>

            <Message v-else-if="integrityError" severity="warn" :closable="false">
              Could not check data integrity: {{ integrityError }}
            </Message>

            <div v-else-if="integrityResult" class="quality-content">
              <div class="quality-summary">
                <div class="quality-stat">
                  <span class="quality-label">Templates Checked</span>
                  <span class="quality-value">{{ integrityResult.summary.total_templates }}</span>
                </div>
                <div class="quality-stat">
                  <span class="quality-label">Documents Checked</span>
                  <span class="quality-value">{{ integrityResult.summary.total_documents }}</span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityResult.summary.templates_with_issues > 0 }">
                  <span class="quality-label">Templates with Issues</span>
                  <span class="quality-value">{{ integrityResult.summary.templates_with_issues }}</span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityResult.summary.documents_with_issues > 0 }">
                  <span class="quality-label">Documents with Issues</span>
                  <span class="quality-value">{{ integrityResult.summary.documents_with_issues }}</span>
                </div>
              </div>

              <div v-if="integrityResult.issues.length > 0" class="quality-issues-summary">
                <Message severity="warn" :closable="false">
                  <div class="issues-message">
                    <span>{{ integrityResult.issues.length }} issue{{ integrityResult.issues.length !== 1 ? 's' : '' }} found</span>
                    <Button
                      label="View Details"
                      icon="pi pi-arrow-right"
                      iconPos="right"
                      text
                      size="small"
                      @click="router.push('/audit')"
                    />
                  </div>
                </Message>
              </div>

              <div v-else class="quality-healthy">
                <i class="pi pi-check-circle"></i>
                <span>All references are valid. No integrity issues found.</span>
              </div>

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

      <!-- Recent Items — 3-column grid -->
      <div class="recent-grid">
        <!-- Recent Terminologies -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-book"></i>
              <span>Recent Terminologies</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTerminologies"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToTerminology(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column field="term_count" header="Terms" style="width: 60px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.term_count }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No terminologies</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Terminologies" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/terminologies')" />
          </template>
        </Card>

        <!-- Recent Templates -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-file"></i>
              <span>Recent Templates</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTemplates"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToTemplate(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column header="Fields" style="width: 60px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.fields?.length || 0 }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No templates</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Templates" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/templates')" />
          </template>
        </Card>

        <!-- Recent Documents -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-folder"></i>
              <span>Recent Documents</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentDocuments"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToDocument(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="document_id" header="ID" style="width: 100px">
                <template #body="{ data }">
                  <code class="doc-id">{{ data.document_id?.slice(0, 8) }}...</code>
                </template>
              </Column>
              <Column field="template_id" header="Template">
                <template #body="{ data }">
                  {{ data.template_id }}
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No documents</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Documents" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/documents')" />
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
  margin-bottom: 1.5rem;
}

.page-header h1 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.subtitle {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
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

/* Compact stat bar */
.stat-bar {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.stat-chip {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  background: var(--p-surface-0);
  border: 1px solid var(--p-surface-200);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  flex: 1;
  min-width: 140px;
}

.stat-chip:hover {
  border-color: var(--p-primary-color);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}

.stat-chip i {
  color: var(--p-primary-color);
  font-size: 1rem;
}

.stat-chip-count {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--p-text-color);
}

.stat-chip-label {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

/* Quick actions row */
.quick-actions-row {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

/* Data quality */
.data-quality-section {
  margin-bottom: 1.5rem;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-title i {
  color: var(--p-primary-color);
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

.quality-label {
  display: block;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin-bottom: 0.25rem;
}

.quality-value {
  display: block;
  font-size: 1.25rem;
  font-weight: 600;
}

.quality-stat.has-issues .quality-value {
  color: var(--p-red-500);
}

.quality-issues-summary :deep(.p-message) {
  margin: 0;
}

.issues-message {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
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

/* Recent grid — 3 columns */
.recent-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.recent-card :deep(.p-card-title) {
  font-size: 0.9375rem;
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

.doc-id {
  font-size: 0.75rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.25rem;
  border-radius: 3px;
}

.empty-state {
  text-align: center;
  padding: 1.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

@media (max-width: 1200px) {
  .recent-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 768px) {
  .recent-grid {
    grid-template-columns: 1fr;
  }

  .stat-bar {
    gap: 0.5rem;
  }

  .stat-chip {
    min-width: calc(50% - 0.25rem);
  }
}
</style>
