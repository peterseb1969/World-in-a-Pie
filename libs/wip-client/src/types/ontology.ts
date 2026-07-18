import type { PaginatedResponse } from './common.js'

export interface TermRelation {
  namespace: string
  source_term_id: string
  target_term_id: string
  relation_type: string
  relation_value?: string
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

export type TermRelationListResponse = PaginatedResponse<TermRelation>

export interface CreateTermRelationRequest {
  source_term_id: string
  target_term_id: string
  relation_type: string
  metadata?: Record<string, unknown>
  created_by?: string
}

export interface DeleteTermRelationRequest {
  source_term_id: string
  target_term_id: string
  relation_type: string
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
  relation_type: string
  direction: string
  nodes: TraversalNode[]
  total: number
  max_depth_reached: boolean
}
