<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import Tree from 'primevue/tree'
import Button from 'primevue/button'
import Select from 'primevue/select'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'
import { useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import type { TraversalNode } from '@/types'

const props = defineProps<{
  termId: string
  termValue?: string
  namespace?: string
}>()

const emit = defineEmits<{
  navigateToTerm: [termId: string]
}>()

const uiStore = useUiStore()

const loading = ref(false)
const direction = ref<'ancestors' | 'descendants'>('ancestors')
const maxDepth = ref(10)
const nodes = ref<TraversalNode[]>([])
const maxDepthReached = ref(false)

const directionOptions = [
  { label: 'Ancestors (upward)', value: 'ancestors' },
  { label: 'Descendants (downward)', value: 'descendants' },
]

const depthOptions = [
  { label: 'Depth: 5', value: 5 },
  { label: 'Depth: 10', value: 10 },
  { label: 'Depth: 20', value: 20 },
  { label: 'Depth: 50', value: 50 },
]

watch(
  [() => props.termId, direction, maxDepth],
  () => loadHierarchy(),
  { immediate: true }
)

async function loadHierarchy() {
  if (!props.termId) return
  loading.value = true
  try {
    const params = { max_depth: maxDepth.value, namespace: props.namespace }
    const data = direction.value === 'ancestors'
      ? await defStoreClient.getAncestors(props.termId, params)
      : await defStoreClient.getTermDescendants(props.termId, params)
    nodes.value = data.nodes
    maxDepthReached.value = data.max_depth_reached
  } catch (e) {
    uiStore.showError('Failed to load hierarchy', (e as Error).message)
    nodes.value = []
  } finally {
    loading.value = false
  }
}

// Build a PrimeVue Tree structure from flat traversal nodes
const treeNodes = computed(() => {
  if (nodes.value.length === 0) return []

  // Root node is the queried term
  const root = {
    key: props.termId,
    label: props.termValue || props.termId,
    data: { term_id: props.termId, depth: 0 },
    children: [] as any[],
    styleClass: 'root-node',
  }

  // Group nodes by depth and build parent-child from path
  const nodeMap: Record<string, any> = { [props.termId]: root }

  // Sort by depth to process parents before children
  const sorted = [...nodes.value].sort((a, b) => a.depth - b.depth)

  for (const node of sorted) {
    const treeNode = {
      key: node.term_id,
      label: node.value || node.term_id,
      data: { term_id: node.term_id, depth: node.depth },
      children: [] as any[],
    }
    nodeMap[node.term_id] = treeNode

    // Find parent from path
    if (node.path.length >= 2) {
      const parentId = node.path[node.path.length - 2]
      const parent = nodeMap[parentId]
      if (parent) {
        parent.children.push(treeNode)
      } else {
        root.children.push(treeNode)
      }
    } else {
      root.children.push(treeNode)
    }
  }

  return [root]
})

const expandedKeys = computed(() => {
  // Expand all nodes by default
  const keys: Record<string, boolean> = {}
  function expand(nodes: any[]) {
    for (const n of nodes) {
      keys[n.key] = true
      if (n.children) expand(n.children)
    }
  }
  expand(treeNodes.value)
  return keys
})
</script>

<template>
  <div class="hierarchy-tree">
    <div class="controls">
      <Select
        v-model="direction"
        :options="directionOptions"
        option-label="label"
        option-value="value"
        class="direction-select"
      />
      <Select
        v-model="maxDepth"
        :options="depthOptions"
        option-label="label"
        option-value="value"
        class="depth-select"
      />
      <Button
        icon="pi pi-refresh"
        severity="secondary"
        text
        rounded
        size="small"
        title="Refresh"
        @click="loadHierarchy"
      />
    </div>

    <div v-if="loading" class="loading-state">
      <ProgressSpinner style="width: 30px; height: 30px" />
      <span>Loading hierarchy...</span>
    </div>

    <div v-else-if="nodes.length === 0" class="empty-state">
      <i class="pi pi-sitemap" style="font-size: 2rem; opacity: 0.3"></i>
      <p>No {{ direction }} found for this term</p>
    </div>

    <template v-else>
      <div class="tree-info">
        <span>{{ nodes.length }} {{ direction }} found</span>
        <Tag v-if="maxDepthReached" value="Max depth reached" severity="warn" />
      </div>

      <Tree
        :value="treeNodes"
        :expandedKeys="expandedKeys"
        class="hierarchy-primevue-tree"
      >
        <template #default="{ node }">
          <span class="tree-node-content">
            <a
              href="#"
              class="node-link"
              :class="{ 'root-link': node.styleClass === 'root-node' }"
              @click.prevent="emit('navigateToTerm', node.data.term_id)"
            >
              {{ node.label }}
            </a>
            <code class="node-id">{{ node.data.term_id }}</code>
            <Tag
              v-if="node.data.depth > 0"
              :value="`depth ${node.data.depth}`"
              severity="secondary"
              class="depth-tag"
            />
          </span>
        </template>
      </Tree>
    </template>
  </div>
</template>

<style scoped>
.hierarchy-tree {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.controls {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.direction-select {
  min-width: 200px;
}

.depth-select {
  min-width: 120px;
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.tree-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.tree-node-content {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.node-link {
  text-decoration: none;
  color: var(--p-primary-color);
  font-weight: 500;
}

.node-link:hover {
  text-decoration: underline;
}

.root-link {
  font-weight: 700;
}

.node-id {
  font-size: 0.7rem;
  color: var(--p-text-muted-color);
  background: var(--p-surface-100);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
}

.depth-tag {
  font-size: 0.65rem;
}
</style>
