# Design: Ontology Browser

**Status:** Ready to implement
**Depends on:** Ontology support (implemented)

---

## Motivation

The WIP Console sidebar has an "Ontology Browser" menu entry that currently links to `?tab=ontology` on the terminology list — which does nothing. The existing ontology UI is good but scattered: relation lists and hierarchy trees live inside individual term/terminology detail views. There's no way to **explore** an ontology by walking the graph.

The existing HierarchyTree component only traverses a single relation type (`is_a`) in a single direction. Ontologies are multi-type, potentially cyclic graphs — `is_a`, `part_of`, `maps_to`, `finding_site` can all coexist. A term can be simultaneously a child of one term via `is_a` and part of another via `part_of`. The tree metaphor breaks down here.

What's needed is an **ego-graph browser**: focus on one entity, see everything it connects to (all relation types), click any neighbour to refocus. This is the standard interaction pattern for ontology exploration (used by EBI OLS, NCBO BioPortal, Protégé).

## Goals

1. Provide a dedicated view for interactive ontology exploration
2. Show all relation types around a focal term (not just `is_a`)
3. Support click-to-navigate: clicking any visible term refocuses the graph on it
4. Configurable neighbourhood depth (default 2 levels)
5. No backend changes required for the initial implementation

## Non-Goals

- Full ontology editing (create/delete relations) — the existing RelationForm and RelationList handle this
- Cross-namespace relation browsing
- Performance for ontologies with 100k+ terms at depth 3+ (defer to server-side endpoint if needed)

---

## Design

### Entry Point

The sidebar "Ontology Browser" menu entry already exists. Wire it to a new route:

```
/ontology → OntologyBrowserView.vue
```

Update `AppLayout.vue` to point to `/ontology` instead of `/terminologies?tab=ontology`.

### View Layout

```
┌─────────────────────────────────────────────────────────┐
│  Ontology Browser                                       │
├──────────┬──────────────────────────────────┬───────────┤
│ Controls │         Graph Canvas             │  Detail   │
│          │                                  │  Panel    │
│ [Termi-  │                                  │           │
│  nology  │        ┌───┐                     │  Term:    │
│  select] │   ┌───→│ B │                     │  {value}  │
│          │   │    └───┘                     │           │
│ [Term    │ ┌─┴─┐          ┌───┐             │  ID:      │
│  search] │ │ A │──is_a───→│ C │             │  {id}     │
│          │ └─┬─┘          └───┘             │           │
│ Depth:   │   │    ┌───┐                     │  Rels:    │
│ [1][2][3]│   └───→│ D │──part_of──→...      │  12 out   │
│          │        └───┘                     │  3 in     │
│ Types:   │                                  │           │
│ ☑ is_a   │                                  │  [Open    │
│ ☑ part_of│                                  │   Detail] │
│ ☐ maps_to│                                  │           │
└──────────┴──────────────────────────────────┴───────────┘
```

**Three panels:**

1. **Controls (left)** — terminology selector, term search (autocomplete), depth slider (1-3, default 2), relation type checkboxes
2. **Graph canvas (centre)** — force-directed graph rendered by Cytoscape.js. Focus term visually emphasised (larger, different colour). Edges labelled with relation type, colour-coded by type.
3. **Detail panel (right)** — shows metadata for the currently hovered or selected node. Includes a link to open the full TermDetailView.

### Interaction

1. **Initial load:** User selects a terminology, then searches for a starting term (or picks from a "root terms" shortlist — terms with no parents).
2. **Focus:** The selected term becomes the focal node. Fetch its neighbourhood to the configured depth. Render the ego-graph.
3. **Click node:** The clicked node becomes the new focus. Fetch its neighbourhood, animate the graph transition. Previous nodes outside the new neighbourhood fade out; new nodes fade in.
4. **Hover node:** Highlight connected edges. Show term details in the right panel.
5. **Filter by type:** Toggling relation type checkboxes shows/hides edges (and orphaned nodes) without re-fetching.
6. **Change depth:** Re-fetch neighbourhood at new depth, re-render.

### Data Fetching — Client-Side Fan-Out

No new backend endpoint needed. Use existing API:

```
Depth 1: GET /ontology/term-relations?term_id={focus}&direction=both
Depth 2: For each neighbour from depth 1:
          GET /ontology/term-relations?term_id={neighbour}&direction=both
Depth 3: Same pattern for depth-2 neighbours
```

**Data structure built client-side:**

```typescript
interface GraphNode {
  id: string           // term_id
  value: string        // term value (display label)
  terminologyId: string
  depth: number        // distance from focus (0 = focus)
  isFocus: boolean
}

interface GraphEdge {
  source: string       // source term_id
  target: string       // target term_id
  type: string         // relation_type
}

interface EgoGraph {
  nodes: Map<string, GraphNode>
  edges: GraphEdge[]
  focusId: string
  maxDepthReached: boolean
}
```

**Performance budget:** A term with 20 direct relations at depth 2 = 1 + 20 = 21 API calls. Each returns in ~10-30ms on localhost. Total: 200-600ms. Acceptable. At depth 3 with branching factor 20, it's 1 + 20 + 400 = 421 calls — too many. Cap depth at 3, and for depth 3, only expand the focus term's direct neighbours (not all depth-2 nodes). Alternatively, add a `neighbourhood` endpoint later if depth 3 is needed.

### Visualization Library

**Cytoscape.js** via the `cytoscape` npm package (~330KB minified).

Rationale:
- Purpose-built for biological network visualization (literally designed for ontology graphs)
- Handles hundreds of nodes smoothly
- Built-in layout algorithms: `cose` (force-directed), `concentric` (focus in centre), `breadthfirst`
- Click/hover/drag events, zoom/pan, node styling
- No Vue wrapper needed — mount to a div ref, manage lifecycle in `onMounted`/`onUnmounted`
- Well-maintained, MIT licensed, used by EBI, KEGG, NDEx

**Layout:** `concentric` with the focus term at centre, depth-1 nodes in the first ring, depth-2 in the second ring. Fall back to `cose` (force-directed) if the graph is too dense for concentric rings.

### Edge Styling

Each relation type gets a distinct colour:

| Type | Colour | Line Style |
|------|--------|------------|
| `is_a` | Blue | Solid |
| `part_of` | Green | Solid |
| `has_part` | Green | Dashed |
| `maps_to` | Orange | Dotted |
| `related_to` | Grey | Dashed |
| `finding_site` | Purple | Solid |
| `causative_agent` | Red | Solid |
| Other | Grey | Solid |

Edges are directed arrows. Labels shown on hover (not permanently, to reduce clutter).

---

## Implementation Plan

### Step 1: Add Cytoscape.js Dependency

```bash
cd ui/wip-console && npm install cytoscape
```

Add TypeScript types: `npm install -D @types/cytoscape` (or use the bundled types — Cytoscape ships its own since v3.19).

### Step 2: Create EgoGraph Component

`src/components/terminologies/EgoGraph.vue`

Props:
- `focusTermId: string` — the current focal term
- `depth: number` — neighbourhood depth (1-3)
- `visibleTypes: string[]` — which relation types to show

Emits:
- `focus(termId: string)` — when a node is clicked (parent refocuses)
- `select(termId: string)` — when a node is selected (detail panel updates)

Responsibilities:
- Mount Cytoscape instance on a container div
- Fetch neighbourhood via `defStoreClient.listRelations()` fan-out
- Build node/edge arrays, apply to Cytoscape
- Handle click → emit `focus`
- Handle filter changes → show/hide elements (no re-fetch)
- Handle depth changes → re-fetch and re-render
- Animate transitions when focus changes

### Step 3: Create OntologyBrowserView

`src/views/terminologies/OntologyBrowserView.vue`

Responsibilities:
- Terminology selector dropdown (fetch via `defStoreClient.listTerminologies()`)
- Term search with autocomplete (fetch via `defStoreClient.listTerms()`)
- Depth control (segmented button: 1, 2, 3)
- Relation type filter checkboxes (populated from edges in current graph)
- Detail panel showing selected node info
- "Open in Term Detail" link
- Manage `focusTermId` state, pass to EgoGraph

### Step 4: Wire Route and Menu

- Add route: `/ontology` → `OntologyBrowserView`
- Update `AppLayout.vue`: change menu href from `/terminologies?tab=ontology` to `/ontology`
- Remove "experimental" label if present

### Step 5: Polish

- Loading spinner during neighbourhood fetch
- Empty state when no terminology selected
- "Too many nodes" warning if graph exceeds ~200 nodes (suggest reducing depth)
- Keyboard navigation: arrow keys to traverse, Enter to focus
- URL state: `/ontology?terminology={id}&term={id}&depth=2` so the view is bookmarkable/shareable

---

## Future Enhancements (Not in Scope)

- **Server-side neighbourhood endpoint** — `GET /ontology/terms/{id}/neighbourhood?depth=2&types=is_a,part_of` returning a pre-built node+edge graph. Eliminates client-side fan-out. Add if depth-3 performance becomes an issue.
- **Shortest path** — highlight the path between two selected terms.
- **Subgraph export** — export the visible graph as OBO Graph JSON or SVG.
- **Colour by terminology** — when cross-terminology relations exist, colour nodes by source terminology.
- **Minimap** — Cytoscape supports a navigator extension for large graphs.
