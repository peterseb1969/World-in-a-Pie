// =============================================================================
// ONTOLOGY / RELATIONSHIP TYPES
// =============================================================================

export interface Relationship {
  namespace: string
  source_term_id: string
  target_term_id: string
  relationship_type: string
  relationship_value?: string
  source_term_value?: string
  source_term_label?: string
  target_term_value?: string
  target_term_label?: string
  source_terminology_id?: string
  target_terminology_id?: string
  metadata: Record<string, unknown>
  status: string
  created_at: string
  created_by?: string
}

export interface RelationshipListResponse {
  items: Relationship[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface CreateRelationshipRequest {
  source_term_id: string
  target_term_id: string
  relationship_type: string
  metadata?: Record<string, unknown>
  created_by?: string
}

export interface DeleteRelationshipRequest {
  source_term_id: string
  target_term_id: string
  relationship_type: string
  hard_delete?: boolean
}

export interface TraversalNode {
  term_id: string
  value?: string
  terminology_id?: string
  depth: number
  path: string[]
}

export interface TraversalResponse {
  term_id: string
  relationship_type: string
  direction: string
  nodes: TraversalNode[]
  total: number
  max_depth_reached: boolean
}
