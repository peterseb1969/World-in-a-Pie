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
  /**
   * Canonical UUID, fully qualified 'ns:terminology:value', or — with
   * source_terminology set — the opaque raw term value (never
   * colon-parsed). The ambiguous 2-part 'TERMINOLOGY:VALUE' shorthand
   * is rejected by the platform (422).
   */
  source_term_id: string
  target_term_id: string
  /**
   * Terminology scoping a value-form source_term_id. Per-item because a
   * relation's two endpoints may live in different terminologies.
   */
  source_terminology?: string
  target_terminology?: string
  relation_type: string
  metadata?: Record<string, unknown>
  created_by?: string
}

export interface DeleteTermRelationRequest {
  source_term_id: string
  target_term_id: string
  source_terminology?: string
  target_terminology?: string
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
