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
