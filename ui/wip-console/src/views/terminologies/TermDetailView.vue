<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Breadcrumb from 'primevue/breadcrumb'
import Card from 'primevue/card'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import Skeleton from 'primevue/skeleton'
import { useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import type { Term, Relationship } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'
import TermForm from '@/components/terminologies/TermForm.vue'
import RelationshipList from '@/components/terminologies/RelationshipList.vue'
import HierarchyTree from '@/components/terminologies/HierarchyTree.vue'

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const uiStore = useUiStore()

const term = ref<Term | null>(null)
const loading = ref(false)
const showEditDialog = ref(false)
const parents = ref<Relationship[]>([])
const children = ref<Relationship[]>([])
const loadingFamily = ref(false)

const breadcrumbHome = { icon: 'pi pi-home', command: () => { router.push('/') } }

const breadcrumbItems = computed(() => {
  const items = [
    { label: 'Terminologies', command: () => { router.push('/terminologies') } },
  ]
  if (term.value) {
    items.push({
      label: term.value.terminology_value || term.value.terminology_id,
      command: () => { router.push(`/terminologies/${term.value!.terminology_id}`) },
    })
    items.push({ label: term.value.label || term.value.value, command: () => {} })
  }
  return items
})

onMounted(() => loadTerm())

watch(() => props.id, () => loadTerm())

async function loadTerm() {
  loading.value = true
  try {
    term.value = await defStoreClient.getTerm(props.id)
    await loadFamily()
  } catch (e) {
    uiStore.showError('Failed to load term', (e as Error).message)
    router.push('/terminologies')
  } finally {
    loading.value = false
  }
}

async function loadFamily() {
  if (!term.value) return
  loadingFamily.value = true
  try {
    const [p, c] = await Promise.all([
      defStoreClient.getParents(props.id),
      defStoreClient.getChildren(props.id),
    ])
    parents.value = p
    children.value = c
  } catch (e) {
    // Non-critical — term might have no relationships
    parents.value = []
    children.value = []
  } finally {
    loadingFamily.value = false
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | undefined {
  switch (status) {
    case 'active': return 'info'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

async function onUpdated() {
  showEditDialog.value = false
  await loadTerm()
}

function navigateToTerm(termId: string) {
  router.push(`/terms/${termId}`)
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString()
}
</script>

<template>
  <div class="term-detail-view">
    <Breadcrumb :model="breadcrumbItems" :home="breadcrumbHome" class="breadcrumb" />

    <div v-if="loading && !term" class="loading-state">
      <Skeleton width="200px" height="2rem" class="mb-2" />
      <Skeleton width="100%" height="150px" />
    </div>

    <template v-else-if="term">
      <div class="header-section">
        <div class="header-info">
          <div class="title-row">
            <h1>{{ term.label || term.value }}</h1>
            <Tag :value="term.status" :severity="getStatusSeverity(term.status)" />
          </div>
          <div class="code-row">
            <code class="code-badge">{{ term.value }}</code>
            <TruncatedId :id="term.term_id" :length="20" />
          </div>
          <p v-if="term.description" class="description">{{ term.description }}</p>
        </div>
        <div class="header-actions">
          <Button
            label="Edit"
            icon="pi pi-pencil"
            severity="secondary"
            @click="showEditDialog = true"
          />
          <Button
            icon="pi pi-arrow-left"
            severity="secondary"
            title="Back to terminology"
            @click="router.push(`/terminologies/${term.terminology_id}`)"
          />
        </div>
      </div>

      <!-- Quick info card -->
      <Card class="info-card">
        <template #content>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">Terminology</span>
              <a
                href="#"
                class="info-value link"
                @click.prevent="router.push(`/terminologies/${term.terminology_id}`)"
              >
                {{ term.terminology_value || term.terminology_id }}
              </a>
            </div>
            <div class="info-item">
              <span class="info-label">Parents</span>
              <span class="info-value">{{ parents.length }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Children</span>
              <span class="info-value">{{ children.length }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Aliases</span>
              <span class="info-value">{{ term.aliases?.length || 0 }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Created</span>
              <span class="info-value">{{ formatDate(term.created_at) }}</span>
            </div>
          </div>
        </template>
      </Card>

      <!-- Parents / Children summary -->
      <div v-if="parents.length > 0 || children.length > 0" class="family-section">
        <div v-if="parents.length > 0" class="family-group">
          <span class="family-label">Parents:</span>
          <Tag
            v-for="p in parents"
            :key="p.target_term_id"
            class="family-tag"
            severity="info"
            @click="navigateToTerm(p.target_term_id)"
          >
            <span class="clickable">{{ p.target_term_value || p.target_term_id }}</span>
          </Tag>
        </div>
        <div v-if="children.length > 0" class="family-group">
          <span class="family-label">Children:</span>
          <Tag
            v-for="c in children.slice(0, 10)"
            :key="c.source_term_id"
            class="family-tag"
            severity="secondary"
            @click="navigateToTerm(c.source_term_id)"
          >
            <span class="clickable">{{ c.source_term_value || c.source_term_id }}</span>
          </Tag>
          <span v-if="children.length > 10" class="more-badge">+{{ children.length - 10 }} more</span>
        </div>
      </div>

      <TabView>
        <TabPanel value="0" header="Details">
          <div class="details-section">
            <div v-if="term.aliases?.length" class="detail-row">
              <span class="detail-label">Aliases</span>
              <div class="aliases">
                <Tag
                  v-for="alias in term.aliases"
                  :key="alias"
                  :value="alias"
                  severity="secondary"
                />
              </div>
            </div>
            <div v-if="term.parent_term_id" class="detail-row">
              <span class="detail-label">Parent (legacy)</span>
              <a href="#" @click.prevent="navigateToTerm(term.parent_term_id!)">
                {{ term.parent_term_id }}
              </a>
            </div>
            <div class="detail-row">
              <span class="detail-label">Sort Order</span>
              <span>{{ term.sort_order }}</span>
            </div>
            <div v-if="term.deprecated_reason" class="detail-row">
              <span class="detail-label">Deprecated Reason</span>
              <span>{{ term.deprecated_reason }}</span>
            </div>
            <div v-if="term.replaced_by_term_id" class="detail-row">
              <span class="detail-label">Replaced By</span>
              <a href="#" @click.prevent="navigateToTerm(term.replaced_by_term_id!)">
                {{ term.replaced_by_term_id }}
              </a>
            </div>
          </div>
        </TabPanel>

        <TabPanel value="1">
          <template #header>
            <span class="tab-header">
              <i class="pi pi-sitemap"></i>
              Relationships
              <Tag
                v-if="parents.length + children.length > 0"
                :value="String(parents.length + children.length)"
                severity="info"
                rounded
              />
            </span>
          </template>
          <RelationshipList
            :terminology-id="term.terminology_id"
            :term-id="term.term_id"
            @navigate-to-term="navigateToTerm"
          />
        </TabPanel>

        <TabPanel value="2" header="Hierarchy">
          <HierarchyTree
            :term-id="term.term_id"
            :term-value="term.label || term.value"
            @navigate-to-term="navigateToTerm"
          />
        </TabPanel>

        <TabPanel value="3" header="Raw JSON">
          <div class="raw-json">
            <pre>{{ JSON.stringify(term, null, 2) }}</pre>
          </div>
        </TabPanel>
      </TabView>
    </template>

    <TermForm
      v-if="term"
      v-model:visible="showEditDialog"
      :terminology-id="term.terminology_id"
      :term="term"
      @updated="onUpdated"
    />
  </div>
</template>

<style scoped>
.term-detail-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.breadcrumb {
  background: transparent;
  padding: 0;
}

.loading-state {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.mb-2 {
  margin-bottom: 0.5rem;
}

.header-section {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}

.header-info {
  flex: 1;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.title-row h1 {
  margin: 0;
  font-size: 1.75rem;
}

.code-row {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-top: 0.5rem;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.875rem;
}

.description {
  margin: 0.75rem 0 0 0;
  color: var(--p-text-muted-color);
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.info-card {
  background: var(--p-surface-card);
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 1.5rem;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.info-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.info-value {
  font-weight: 500;
}

.info-value.link {
  color: var(--p-primary-color);
  text-decoration: none;
  cursor: pointer;
}

.info-value.link:hover {
  text-decoration: underline;
}

.family-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.family-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.family-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--p-text-muted-color);
  min-width: 70px;
}

.family-tag {
  cursor: pointer;
}

.clickable {
  cursor: pointer;
}

.more-badge {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}

.tab-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.details-section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem 0;
}

.detail-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.aliases {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.raw-json pre {
  background-color: var(--p-surface-100);
  padding: 1rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  overflow-x: auto;
  margin: 0;
  max-height: 600px;
}
</style>
