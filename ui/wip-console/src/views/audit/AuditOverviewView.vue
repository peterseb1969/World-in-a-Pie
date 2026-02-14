<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Checkbox from 'primevue/checkbox'
import { useAuthStore, useUiStore } from '@/stores'
import { isReportingEnabled } from '@/config/modules'
import {
  reportingSyncClient,
  type ActivityItem,
  type IntegrityCheckResult,
  type IntegrityIssue
} from '@/api/client'

const router = useRouter()
const authStore = useAuthStore()
const uiStore = useUiStore()

// Check if reporting module is enabled
const reportingEnabled = isReportingEnabled()

// Loading states
const loading = ref(true)
const integrityLoading = ref(false)

// Data
const activities = ref<ActivityItem[]>([])
const integrityResult = ref<IntegrityCheckResult | null>(null)
const integrityError = ref<string | null>(null)

// Filters
const selectedTypes = ref<string[]>(['terminology', 'term', 'template', 'document', 'file'])
const activityLimit = ref(100)
const activitySearch = ref('')

// Type options for filter
const typeOptions = [
  { label: 'Terminologies', value: 'terminology' },
  { label: 'Terms', value: 'term' },
  { label: 'Templates', value: 'template' },
  { label: 'Documents', value: 'document' },
  { label: 'Files', value: 'file' }
]

// Activity counts by type
const activityCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const activity of activities.value) {
    counts[activity.type] = (counts[activity.type] || 0) + 1
  }
  return counts
})

// Filtered activities based on selected types and search
const filteredActivities = computed(() => {
  let result = activities.value.filter(a => selectedTypes.value.includes(a.type))

  if (activitySearch.value.trim()) {
    const search = activitySearch.value.toLowerCase()
    result = result.filter(a =>
      (a.entity_label && a.entity_label.toLowerCase().includes(search)) ||
      (a.entity_value && a.entity_value.toLowerCase().includes(search)) ||
      (a.entity_id && a.entity_id.toLowerCase().includes(search)) ||
      (a.user && a.user.toLowerCase().includes(search))
    )
  }

  return result
})

async function loadActivity() {
  if (!authStore.isAuthenticated) {
    loading.value = false
    return
  }

  loading.value = true
  try {
    const response = await reportingSyncClient.getRecentActivity({
      limit: activityLimit.value
    })
    activities.value = response.activities
  } catch (error) {
    console.error('Failed to load activity:', error)
    uiStore.showError('Failed to load activity', error instanceof Error ? error.message : 'Unknown error')
  } finally {
    loading.value = false
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

function getTypeSeverity(type: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
  switch (type) {
    case 'terminology': return 'info'
    case 'term': return 'secondary'
    case 'template': return 'success'
    case 'document': return 'warn'
    case 'file': return 'danger'
    default: return 'secondary'
  }
}

function getActionIcon(action: string): string {
  switch (action) {
    case 'created': return 'pi pi-plus-circle'
    case 'updated': return 'pi pi-pencil'
    case 'deleted': return 'pi pi-trash'
    case 'deprecated': return 'pi pi-exclamation-triangle'
    default: return 'pi pi-circle'
  }
}

function getActionClass(action: string): string {
  switch (action) {
    case 'created': return 'action-created'
    case 'updated': return 'action-updated'
    case 'deleted': return 'action-deleted'
    case 'deprecated': return 'action-deprecated'
    default: return ''
  }
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
  })
}

function formatFullTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function openInExplorer(params: { q?: string; type?: string; id?: string }) {
  router.push({ path: '/audit/explorer', query: params })
}

function navigateToEntity(activity: ActivityItem) {
  // Open in explorer with entity type and ID for inspection
  openInExplorer({
    type: activity.type,
    id: activity.entity_id
  })
}

function navigateToIssue(issue: IntegrityIssue) {
  // Determine entity type from source
  let entityType = 'document'
  if (issue.source === 'template-store') {
    entityType = 'template'
  }

  // Open in explorer with entity type and ID for inspection
  openInExplorer({
    type: entityType,
    id: issue.entity_id
  })
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

onMounted(() => {
  if (reportingEnabled) {
    loadActivity()
    loadIntegrityCheck()
  }
})

// Reload when auth changes
watch(
  () => authStore.isAuthenticated,
  (isAuth, wasAuth) => {
    if (reportingEnabled && isAuth && !wasAuth) {
      loadActivity()
      loadIntegrityCheck()
    }
  }
)
</script>

<template>
  <div class="audit-overview">
    <div class="page-header">
      <h1>Audit Trail</h1>
      <p class="subtitle">Monitor changes and data quality across all entities</p>
    </div>

    <!-- Reporting module not enabled -->
    <div v-if="!reportingEnabled" class="module-warning">
      <Card>
        <template #content>
          <div class="warning-content">
            <i class="pi pi-info-circle"></i>
            <div>
              <h3>Reporting Module Not Enabled</h3>
              <p>The audit trail requires the reporting module to be enabled.</p>
              <p class="hint">Add <code>reporting</code> to WIP_MODULES in your .env file and restart the stack.</p>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <!-- Auth warning -->
    <div v-else-if="!authStore.isAuthenticated" class="auth-warning">
      <Card>
        <template #content>
          <div class="warning-content">
            <i class="pi pi-exclamation-triangle"></i>
            <div>
              <h3>Authentication Required</h3>
              <p>Please log in to view the audit trail.</p>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <template v-else>
      <!-- Summary Cards -->
      <div class="summary-grid">
        <!-- Data Quality Card -->
        <Card class="summary-card quality-card">
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
            <div v-if="integrityLoading" class="loading-state">
              <ProgressSpinner style="width: 24px; height: 24px" />
              <span>Checking...</span>
            </div>
            <Message v-else-if="integrityError" severity="warn" :closable="false" class="compact-message">
              {{ integrityError }}
            </Message>
            <div v-else-if="integrityResult" class="quality-stats">
              <div class="quality-stat">
                <span class="label">Issues</span>
                <span class="value" :class="{ 'has-issues': integrityResult.issues.length > 0 }">
                  {{ integrityResult.issues.length }}
                </span>
              </div>
              <div class="quality-stat">
                <span class="label">Templates</span>
                <span class="value">{{ integrityResult.summary.total_templates }}</span>
              </div>
              <div class="quality-stat">
                <span class="label">Documents</span>
                <span class="value">{{ integrityResult.summary.total_documents }}</span>
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

        <!-- Activity Count Cards -->
        <Card class="summary-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-history"></i>
              <span>Recent Activity</span>
            </div>
          </template>
          <template #content>
            <div class="activity-counts">
              <div
                v-for="opt in typeOptions"
                :key="opt.value"
                class="count-item"
              >
                <Tag :severity="getTypeSeverity(opt.value)" class="type-tag">
                  {{ opt.label }}
                </Tag>
                <span class="count">{{ activityCounts[opt.value] || 0 }}</span>
              </div>
            </div>
          </template>
        </Card>

        <!-- Quick Actions -->
        <Card class="summary-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-bolt"></i>
              <span>Quick Actions</span>
            </div>
          </template>
          <template #content>
            <div class="quick-actions">
              <Button
                label="Open Explorer"
                icon="pi pi-search"
                severity="secondary"
                @click="router.push('/audit/explorer')"
              />
              <Button
                label="View Dashboard"
                icon="pi pi-home"
                severity="secondary"
                text
                @click="router.push('/')"
              />
            </div>
          </template>
        </Card>
      </div>

      <!-- Data Quality Issues (detailed) -->
      <Card v-if="integrityResult && integrityResult.issues.length > 0" class="issues-card">
        <template #title>
          <div class="card-title">
            <i class="pi pi-exclamation-circle"></i>
            <span>Data Quality Issues</span>
            <Tag severity="danger" class="issues-count">
              {{ integrityResult.issues.length }}
            </Tag>
          </div>
        </template>
        <template #content>
          <DataTable
            :value="integrityResult.issues"
            :paginator="integrityResult.issues.length > 10"
            :rows="10"
            :rowsPerPageOptions="[10, 25, 50]"
            size="small"
            @row-click="(e) => navigateToIssue(e.data)"
            class="issues-table"
            :pt="{ bodyRow: { style: 'cursor: pointer' } }"
          >
            <Column field="type" header="Type" style="width: 180px">
              <template #body="{ data }">
                <Tag :severity="getIssueSeverity(data.severity)" size="small">
                  {{ data.type.replace(/_/g, ' ') }}
                </Tag>
              </template>
            </Column>
            <Column field="source" header="Source" style="width: 120px">
              <template #body="{ data }">
                <span class="source">{{ data.source.replace('-store', '') }}</span>
              </template>
            </Column>
            <Column field="entity_value" header="Entity" style="width: 140px">
              <template #body="{ data }">
                <span class="entity">{{ data.entity_value || data.entity_id.substring(0, 12) }}</span>
              </template>
            </Column>
            <Column field="field_path" header="Field" style="width: 120px">
              <template #body="{ data }">
                <code v-if="data.field_path" class="field-path">{{ data.field_path }}</code>
                <span v-else class="na">-</span>
              </template>
            </Column>
            <Column field="message" header="Message" />
          </DataTable>
        </template>
      </Card>

      <!-- Activity Feed -->
      <Card class="activity-card">
        <template #title>
          <div class="card-title-row">
            <div class="card-title">
              <i class="pi pi-list"></i>
              <span>Activity Feed</span>
            </div>
            <div class="filter-controls">
              <span class="p-input-icon-left search-wrapper">
                <i class="pi pi-search" />
                <InputText
                  v-model="activitySearch"
                  placeholder="Search activity..."
                  class="activity-search"
                />
              </span>
              <div class="type-filters">
                <div
                  v-for="opt in typeOptions"
                  :key="opt.value"
                  class="filter-item"
                >
                  <Checkbox
                    v-model="selectedTypes"
                    :inputId="opt.value"
                    :value="opt.value"
                  />
                  <label :for="opt.value">{{ opt.label }}</label>
                </div>
              </div>
              <Button
                icon="pi pi-refresh"
                text
                rounded
                size="small"
                :loading="loading"
                @click="loadActivity"
                v-tooltip.left="'Refresh'"
              />
            </div>
          </div>
        </template>
        <template #content>
          <div v-if="loading" class="loading-state">
            <ProgressSpinner style="width: 30px; height: 30px" />
            <span>Loading activity...</span>
          </div>

          <DataTable
            v-else
            :value="filteredActivities"
            :paginator="filteredActivities.length > 20"
            :rows="20"
            :rowsPerPageOptions="[20, 50, 100]"
            size="small"
            @row-click="(e) => navigateToEntity(e.data)"
            class="activity-table"
            :pt="{ bodyRow: { style: 'cursor: pointer' } }"
          >
            <Column header="Action" style="width: 140px">
              <template #body="{ data }">
                <div class="action-cell" :class="getActionClass(data.action)">
                  <i :class="getActionIcon(data.action)"></i>
                  <span>{{ data.action }}</span>
                </div>
              </template>
            </Column>
            <Column header="Type" style="width: 120px">
              <template #body="{ data }">
                <Tag :severity="getTypeSeverity(data.type)" size="small">
                  {{ data.type }}
                </Tag>
              </template>
            </Column>
            <Column header="Entity">
              <template #body="{ data }">
                <div class="entity-cell">
                  <span class="entity-name">{{ data.entity_label || data.entity_value || data.entity_id }}</span>
                  <span v-if="data.entity_value && data.entity_label" class="entity-code">
                    {{ data.entity_value }}
                  </span>
                </div>
              </template>
            </Column>
            <Column header="Version" style="width: 80px">
              <template #body="{ data }">
                <span v-if="data.version" class="version">v{{ data.version }}</span>
                <span v-else class="version-na">-</span>
              </template>
            </Column>
            <Column header="User" style="width: 150px">
              <template #body="{ data }">
                <span class="user">{{ data.user || '-' }}</span>
              </template>
            </Column>
            <Column header="Time" style="width: 120px">
              <template #body="{ data }">
                <span class="timestamp" :title="formatFullTimestamp(data.timestamp)">
                  {{ formatTimestamp(data.timestamp) }}
                </span>
              </template>
            </Column>
            <template #empty>
              <div class="empty-state">
                <i class="pi pi-inbox"></i>
                <p>No recent activity found</p>
              </div>
            </template>
          </DataTable>
        </template>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.audit-overview {
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

/* Module/Auth warning */
.module-warning,
.auth-warning {
  margin-bottom: 2rem;
}

.warning-content .hint {
  margin-top: 0.5rem;
  font-size: 0.875rem;
}

.warning-content code {
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.875rem;
}

.module-warning .warning-content i {
  color: var(--p-blue-500);
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

/* Summary grid */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}

.summary-card :deep(.p-card-title) {
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

.status-tag {
  margin-left: auto;
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.compact-message {
  margin: 0;
}

/* Quality stats */
.quality-stats {
  display: flex;
  gap: 1.5rem;
}

.quality-stat {
  text-align: center;
}

.quality-stat .label {
  display: block;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin-bottom: 0.25rem;
}

.quality-stat .value {
  display: block;
  font-size: 1.5rem;
  font-weight: 600;
}

.quality-stat .value.has-issues {
  color: var(--p-red-500);
}

/* Activity counts */
.activity-counts {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.count-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.type-tag {
  min-width: 100px;
}

.count {
  font-weight: 600;
  color: var(--p-text-color);
}

/* Quick actions */
.quick-actions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.quick-actions :deep(.p-button) {
  justify-content: flex-start;
}

/* Issues card */
.issues-card {
  margin-bottom: 2rem;
}

.issues-card :deep(.p-card-title) {
  font-size: 1rem;
}

.issues-card :deep(.p-card-content) {
  padding: 0;
}

.issues-card .card-title i {
  color: var(--p-red-500);
}

.issues-count {
  margin-left: auto;
}

.issues-table :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.source {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  text-transform: capitalize;
}

.entity {
  font-family: monospace;
  font-size: 0.875rem;
}

.field-path {
  font-size: 0.75rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.na {
  color: var(--p-surface-400);
}

/* Activity card */
.activity-card :deep(.p-card-title) {
  font-size: 1rem;
}

.activity-card :deep(.p-card-content) {
  padding: 0;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.search-wrapper {
  display: inline-flex;
}

.activity-search {
  width: 200px;
}

.type-filters {
  display: flex;
  gap: 1rem;
}

.filter-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.875rem;
}

.filter-item label {
  cursor: pointer;
}

/* Activity table */
.activity-table :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.action-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  text-transform: capitalize;
}

.action-cell i {
  font-size: 1rem;
}

.action-created {
  color: var(--p-green-600);
}

.action-updated {
  color: var(--p-blue-600);
}

.action-deleted {
  color: var(--p-red-600);
}

.action-deprecated {
  color: var(--p-orange-600);
}

.entity-cell {
  display: flex;
  flex-direction: column;
}

.entity-name {
  font-weight: 500;
}

.entity-code {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  font-family: monospace;
}

.version {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.version-na {
  color: var(--p-surface-400);
}

.user {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.timestamp {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 3rem;
  margin-bottom: 1rem;
  display: block;
}

.empty-state p {
  margin: 0;
}

@media (max-width: 768px) {
  .card-title-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .filter-controls {
    flex-direction: column;
    align-items: flex-start;
  }

  .type-filters {
    flex-wrap: wrap;
  }

  .activity-search {
    width: 100%;
  }
}
</style>
