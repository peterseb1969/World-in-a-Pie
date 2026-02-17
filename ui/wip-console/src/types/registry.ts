// =============================================================================
// REGISTRY TYPES
// =============================================================================

export interface RegistryEntry {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  synonyms_count: number
  additional_ids_count: number
  status: 'active' | 'reserved' | 'inactive'
  created_at: string
  created_by: string | null
  updated_at: string
}

export interface RegistryEntryListResponse {
  items: RegistryEntry[]
  total: number
  page: number
  page_size: number
}

export interface RegistryEntryDetail {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  primary_composite_key_hash: string
  synonyms: RegistrySynonym[]
  additional_ids: Array<Record<string, string>>
  source_info: RegistrySourceInfo | null
  search_values: string[]
  metadata: Record<string, unknown>
  status: string
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
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

export interface RegistrySourceInfo {
  system_id: string
  endpoint_url: string | null
}

export interface RegistryLookupResponse {
  input_index: number
  status: string
  preferred_id: string | null
  namespace: string | null
  entity_type: string | null
  additional_ids: Array<Record<string, string>>
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

// =============================================================================
// UNIFIED SEARCH TYPES
// =============================================================================

export interface RegistrySearchResult {
  entry_id: string
  namespace: string
  entity_type: string
  status: string
  is_preferred: boolean
  primary_composite_key: Record<string, unknown>
  synonyms: RegistrySynonym[]
  additional_ids: Array<Record<string, string>>
  source_info: RegistrySourceInfo | null
  metadata: Record<string, unknown>
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  matched_via: 'entry_id' | 'additional_id' | 'composite_key_value' | 'synonym_key_value'
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

// =============================================================================
// ENTRY DETAIL TYPES
// =============================================================================

export interface RegistryEntryFull {
  entry_id: string
  namespace: string
  entity_type: string
  is_preferred: boolean
  primary_composite_key: Record<string, unknown>
  primary_composite_key_hash: string
  synonyms: RegistrySynonym[]
  additional_ids: Array<Record<string, string>>
  source_info: RegistrySourceInfo | null
  search_values: string[]
  metadata: Record<string, unknown>
  status: string
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
}

// =============================================================================
// MUTATION TYPES
// =============================================================================

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
