<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import cytoscape from 'cytoscape'
import type { Core, EventObject } from 'cytoscape'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'
import { useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import type { Relationship } from '@/types'

const props = defineProps<{
  focusTermId: string
  focusLabel?: string
  depth: number
  visibleTypes: string[]
  namespace?: string
}>()

const emit = defineEmits<{
  focus: [termId: string]
  select: [termId: string, value: string]
  typesDiscovered: [types: string[]]
}>()

const uiStore = useUiStore()

const containerRef = ref<HTMLDivElement>()
const loading = ref(false)
const nodeCount = ref(0)
const edgeCount = ref(0)
const maxDepthReached = ref(false)

let cy: Core | null = null

// Edge colours by relationship type
const TYPE_COLOURS: Record<string, string> = {
  is_a: '#4a90d9',
  has_subtype: '#4a90d9',
  part_of: '#27ae60',
  has_part: '#27ae60',
  maps_to: '#e67e22',
  mapped_from: '#e67e22',
  related_to: '#95a5a6',
  finding_site: '#8e44ad',
  causative_agent: '#c0392b',
}

const TYPE_LINE_STYLES: Record<string, string> = {
  has_part: 'dashed',
  maps_to: 'dotted',
  mapped_from: 'dotted',
  related_to: 'dashed',
}

function getTypeColour(type: string): string {
  return TYPE_COLOURS[type] || '#95a5a6'
}

function getTypeLineStyle(type: string): string {
  return TYPE_LINE_STYLES[type] || 'solid'
}

// -------------------------------------------------------------------------
// Data fetching — client-side fan-out
// -------------------------------------------------------------------------

interface GraphNode {
  id: string
  value: string
  terminologyId?: string
  namespace?: string
  depth: number
  isFocus: boolean
}

interface GraphEdge {
  source: string
  target: string
  type: string
}

async function fetchNeighbourhood(
  focusId: string,
  depth: number
): Promise<{ nodes: Map<string, GraphNode>; edges: GraphEdge[]; allTypes: Set<string> }> {
  const nodes = new Map<string, GraphNode>()
  const edges: GraphEdge[] = []
  const edgeSet = new Set<string>() // dedup
  const allTypes = new Set<string>()

  // Seed the focus node — use the selected namespace as starting point
  nodes.set(focusId, { id: focusId, value: props.focusLabel || focusId, depth: 0, isFocus: true, namespace: props.namespace })

  // BFS layer by layer — each node tracks its own namespace
  let frontier = new Set([focusId])

  for (let d = 0; d < depth; d++) {
    if (frontier.size === 0) break

    // Group frontier terms by namespace to minimise API calls
    const byNamespace = new Map<string | undefined, string[]>()
    for (const termId of frontier) {
      const ns = nodes.get(termId)?.namespace
      if (!byNamespace.has(ns)) byNamespace.set(ns, [])
      byNamespace.get(ns)!.push(termId)
    }

    // Fetch relationships for each term, using its own namespace
    const fetches: Promise<{ items: Relationship[]; total: number; page: number; page_size: number; pages: number }>[] = []
    for (const [ns, termIds] of byNamespace) {
      for (const termId of termIds) {
        fetches.push(
          defStoreClient.listRelationships({
            term_id: termId,
            direction: 'both',
            page_size: 100,
            namespace: ns,
          }).catch(() => ({ items: [] as Relationship[], total: 0, page: 1, page_size: 100, pages: 1 }))
        )
      }
    }

    const results = await Promise.all(fetches)
    const nextFrontier = new Set<string>()

    for (const result of results) {
      for (const rel of result.items) {
        const edgeKey = `${rel.source_term_id}|${rel.target_term_id}|${rel.relationship_type}`
        if (edgeSet.has(edgeKey)) continue
        edgeSet.add(edgeKey)

        edges.push({
          source: rel.source_term_id,
          target: rel.target_term_id,
          type: rel.relationship_type,
        })
        allTypes.add(rel.relationship_type)

        // Add discovered nodes — inherit namespace from the relationship
        for (const [id, label, val] of [
          [rel.source_term_id, rel.source_term_label, rel.source_term_value],
          [rel.target_term_id, rel.target_term_label, rel.target_term_value],
        ] as [string, string | undefined, string | undefined][]) {
          const displayName = label || val || id
          if (!nodes.has(id)) {
            nodes.set(id, {
              id,
              value: displayName,
              namespace: rel.namespace,
              depth: d + 1,
              isFocus: false,
            })
            nextFrontier.add(id)
          } else if (displayName !== id && nodes.get(id)!.value === id) {
            nodes.get(id)!.value = displayName
          }
        }
      }
    }

    // Only expand nodes we haven't seen before
    frontier = nextFrontier
  }

  return { nodes, edges, allTypes }
}

// -------------------------------------------------------------------------
// Cytoscape rendering
// -------------------------------------------------------------------------

function initCytoscape() {
  if (!containerRef.value) return

  cy = cytoscape({
    container: containerRef.value,
    style: [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          'text-valign': 'bottom',
          'text-halign': 'center',
          'font-size': '11px',
          'text-margin-y': 6,
          'background-color': '#6c757d',
          width: 28,
          height: 28,
          'border-width': 2,
          'border-color': '#dee2e6',
          color: '#495057',
          'text-max-width': '120px',
          'text-wrap': 'ellipsis',
        },
      },
      {
        selector: 'node[?isFocus]',
        style: {
          'background-color': '#4a90d9',
          'border-color': '#2c6fbb',
          'border-width': 3,
          width: 40,
          height: 40,
          'font-size': '13px',
          'font-weight': 'bold' as any,
          color: '#1a3a5c',
        },
      },
      {
        selector: 'node.depth-1',
        style: {
          'background-color': '#5dade2',
          'border-color': '#3498db',
          width: 32,
          height: 32,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-color': '#e67e22',
          'border-width': 3,
          'background-color': '#f39c12',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 2,
          'line-color': '#ccc',
          'target-arrow-color': '#ccc',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'arrow-scale': 0.8,
          label: '',
          'font-size': '9px',
          'text-rotation': 'autorotate',
          color: '#999',
          'text-background-color': '#fff',
          'text-background-opacity': 0.8,
          'text-background-padding': '2px',
        },
      },
      {
        selector: 'edge:selected',
        style: {
          width: 3,
          label: 'data(type)',
        },
      },
      {
        selector: 'node.hover-connected',
        style: {
          'border-color': '#e67e22',
          'border-width': 3,
        },
      },
      {
        selector: 'edge.hover-connected',
        style: {
          width: 3,
          label: 'data(type)',
        },
      },
      {
        selector: '.hidden-type',
        style: {
          display: 'none',
        },
      },
    ],
    layout: { name: 'grid' }, // placeholder, will be replaced
    minZoom: 0.3,
    maxZoom: 3,
    wheelSensitivity: 0.3,
  })

  // Click to focus
  cy.on('tap', 'node', (evt: EventObject) => {
    const id = evt.target.id()
    emit('focus', id)
  })

  // Hover highlight
  cy.on('mouseover', 'node', (evt: EventObject) => {
    const node = evt.target
    const connected = node.connectedEdges().connectedNodes()
    node.connectedEdges().addClass('hover-connected')
    connected.addClass('hover-connected')
    node.addClass('hover-connected')

    const data = node.data()
    emit('select', data.id, data.label)
  })

  cy.on('mouseout', 'node', () => {
    cy!.elements().removeClass('hover-connected')
  })
}

async function renderGraph() {
  if (!cy) return

  loading.value = true
  maxDepthReached.value = false

  try {
    const { nodes, edges, allTypes } = await fetchNeighbourhood(props.focusTermId, props.depth)

    nodeCount.value = nodes.size
    edgeCount.value = edges.length
    emit('typesDiscovered', [...allTypes])

    // If we had a huge fan-out, flag it
    if (nodes.size > 200) {
      maxDepthReached.value = true
    }

    // Build cytoscape elements
    const cyNodes = [...nodes.values()].map(n => ({
      data: {
        id: n.id,
        label: n.value,
        isFocus: n.isFocus,
        depth: n.depth,
      },
      classes: n.depth === 1 ? 'depth-1' : '',
    }))

    const cyEdges = edges.map((e, i) => ({
      data: {
        id: `e${i}`,
        source: e.source,
        target: e.target,
        type: e.type,
      },
      style: {
        'line-color': getTypeColour(e.type),
        'target-arrow-color': getTypeColour(e.type),
        'line-style': getTypeLineStyle(e.type),
      },
    }))

    cy.elements().remove()
    cy.add([...cyNodes, ...cyEdges])

    // Apply type visibility
    applyTypeFilter()

    // Layout
    const layout = cy.layout({
      name: 'concentric',
      concentric: (node: any) => {
        if (node.data('isFocus')) return 100
        return 100 - node.data('depth') * 30
      },
      levelWidth: () => 1,
      minNodeSpacing: 50,
      animate: true,
      animationDuration: 400,
    } as any)

    layout.run()

    // Fit after layout settles
    setTimeout(() => {
      cy?.fit(undefined, 40)
    }, 500)
  } catch (e) {
    uiStore.showError('Failed to load neighbourhood', (e as Error).message)
  } finally {
    loading.value = false
  }
}

function applyTypeFilter() {
  if (!cy) return
  cy.edges().forEach(edge => {
    const type = edge.data('type')
    if (props.visibleTypes.length > 0 && !props.visibleTypes.includes(type)) {
      edge.addClass('hidden-type')
      // Hide orphaned nodes (nodes with all edges hidden)
    } else {
      edge.removeClass('hidden-type')
    }
  })

  // Hide nodes that have no visible edges (except focus)
  cy.nodes().forEach(node => {
    if (node.data('isFocus')) {
      node.removeClass('hidden-type')
      return
    }
    const visibleEdges = node.connectedEdges().filter(e => !e.hasClass('hidden-type'))
    if (visibleEdges.length === 0) {
      node.addClass('hidden-type')
    } else {
      node.removeClass('hidden-type')
    }
  })
}

// Watch for changes
watch(() => props.focusTermId, () => renderGraph())
watch(() => props.depth, () => renderGraph())
watch(() => props.namespace, () => renderGraph())
watch(() => props.visibleTypes, () => applyTypeFilter(), { deep: true })

onMounted(async () => {
  await nextTick()
  initCytoscape()
  if (props.focusTermId) {
    renderGraph()
  }
})

onUnmounted(() => {
  cy?.destroy()
  cy = null
})

defineExpose({ refresh: renderGraph })
</script>

<template>
  <div class="ego-graph-wrapper">
    <div v-if="loading" class="graph-loading-overlay">
      <ProgressSpinner style="width: 40px; height: 40px" />
      <span>Loading neighbourhood...</span>
    </div>

    <div ref="containerRef" class="graph-container"></div>

    <div class="graph-status-bar">
      <span>{{ nodeCount }} terms, {{ edgeCount }} relationships</span>
      <Tag v-if="maxDepthReached" value="Large graph — consider reducing depth" severity="warn" />
      <span class="graph-hint">Click a term to refocus. Hover to highlight connections.</span>
    </div>
  </div>
</template>

<style scoped>
.ego-graph-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 400px;
}

.graph-container {
  flex: 1;
  min-height: 400px;
  border: 1px solid var(--p-surface-200);
  border-radius: 6px;
  background: var(--p-surface-0);
}

.graph-loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  background: rgba(255, 255, 255, 0.85);
  z-index: 10;
  border-radius: 6px;
  color: var(--p-text-muted-color);
}

.graph-status-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0;
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}

.graph-hint {
  margin-left: auto;
  font-style: italic;
}
</style>
