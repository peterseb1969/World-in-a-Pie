import type { PaginatedResponse } from './common.js'

export interface Namespace {
  prefix: string
  description: string
  isolation_mode: 'open' | 'strict'
  allowed_external_refs: string[]
  id_config: Record<string, unknown>
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
}

export interface NamespaceStats {
  prefix: string
  description: string
  isolation_mode: string
  status: string
  entity_counts: Record<string, number>
}

export interface IdAlgorithmConfig {
  algorithm: 'uuid7' | 'prefixed' | 'nanoid'
  prefix?: string
  pad?: number
  length?: number
}

export interface CreateNamespaceRequest {
  prefix: string
  description?: string
  isolation_mode?: 'open' | 'strict'
  allowed_external_refs?: string[]
  id_config?: Record<string, IdAlgorithmConfig>
  created_by?: string
}

export interface RegistryEntry {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  synonyms_count: number
  status: 'active' | 'reserved' | 'inactive'
  created_at: string
  created_by: string | null
  updated_at: string
}

export type RegistryEntryListResponse = PaginatedResponse<RegistryEntry>

export interface RegistrySourceInfo {
  system_id: string
  endpoint_url: string | null
}

export interface RegistrySynonym {
  namespace: string
  entity_type: string
  composite_key: Record<string, unknown>
  composite_key_hash: string
  source_info: RegistrySourceInfo | null
  created_at: string
  created_by: string | null
}

export interface RegistryEntryFull {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  primary_composite_key_hash: string
  synonyms: RegistrySynonym[]
  source_info: RegistrySourceInfo | null
  search_values: string[]
  metadata: Record<string, unknown>
  status: string
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
}

export interface RegistryLookupResponse {
  input_index: number
  status: string
  entry_id: string | null
  namespace: string | null
  entity_type: string | null
  matched_namespace: string | null
  matched_entity_type: string | null
  matched_composite_key: Record<string, unknown> | null
  matched_via: string | null
  synonyms: RegistrySynonym[]
  source_info: RegistrySourceInfo | null
  source_data: Record<string, unknown> | null
  error: string | null
}

export interface RegistryBrowseParams {
  namespace?: string
  entity_type?: string
  status?: string
  q?: string
  page?: number
  page_size?: number
}

export interface RegistrySearchResult {
  entry_id: string
  namespace: string
  entity_type: string
  status: string
  primary_composite_key: Record<string, unknown>
  synonyms: RegistrySynonym[]
  source_info: RegistrySourceInfo | null
  metadata: Record<string, unknown>
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  matched_via: 'entry_id' | 'composite_key_value' | 'synonym_key_value'
  matched_value: string
  resolution_path: string
}

export interface RegistrySearchResponse {
  items: RegistrySearchResult[]
  total: number
  page: number
  page_size: number
  query: string
}

export interface RegistrySearchParams {
  q: string
  namespace?: string
  entity_type?: string
  status?: string
  page?: number
  page_size?: number
}

export interface AddSynonymRequest {
  target_id: string
  synonym_namespace: string
  synonym_entity_type: string
  synonym_composite_key: Record<string, unknown>
  synonym_source_info?: { system_id: string; endpoint_url?: string }
  created_by?: string
}

export interface RemoveSynonymRequest {
  target_id: string
  synonym_namespace: string
  synonym_entity_type: string
  synonym_composite_key: Record<string, unknown>
  updated_by?: string
}

export interface MergeRequest {
  preferred_id: string
  deprecated_id: string
  updated_by?: string
}
