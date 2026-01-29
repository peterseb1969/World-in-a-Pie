import axios, { AxiosInstance, AxiosError } from 'axios'
import type {
  Terminology,
  TerminologyListResponse,
  CreateTerminologyRequest,
  UpdateTerminologyRequest,
  Term,
  TermListResponse,
  CreateTermRequest,
  UpdateTermRequest,
  DeprecateTermRequest,
  BulkCreateTermRequest,
  BulkOperationResponse,
  ImportTerminologyRequest,
  ExportTerminologyResponse,
  ValidateValueRequest,
  ValidateValueResponse,
  BulkValidateRequest,
  BulkValidateResponse,
  ApiError
} from '@/types'

class DefStoreClient {
  private client: AxiosInstance
  private apiKey: string = ''

  constructor() {
    this.client = axios.create({
      baseURL: '/api/def-store',
      headers: {
        'Content-Type': 'application/json'
      }
    })

    this.client.interceptors.request.use((config) => {
      if (this.apiKey) {
        config.headers['X-API-Key'] = this.apiKey
      }
      return config
    })

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError<ApiError>) => {
        const message = error.response?.data?.detail || error.message
        return Promise.reject(new Error(message))
      }
    )
  }

  setApiKey(key: string) {
    this.apiKey = key
  }

  getApiKey(): string {
    return this.apiKey
  }

  // ===========================================================================
  // TERMINOLOGY ENDPOINTS
  // ===========================================================================

  async listTerminologies(params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
  }): Promise<TerminologyListResponse> {
    const response = await this.client.get<TerminologyListResponse>('/terminologies', { params })
    return response.data
  }

  async getTerminology(id: string): Promise<Terminology> {
    const response = await this.client.get<Terminology>(`/terminologies/${id}`)
    return response.data
  }

  async createTerminology(data: CreateTerminologyRequest): Promise<Terminology> {
    const response = await this.client.post<Terminology>('/terminologies', data)
    return response.data
  }

  async updateTerminology(id: string, data: UpdateTerminologyRequest): Promise<Terminology> {
    const response = await this.client.put<Terminology>(`/terminologies/${id}`, data)
    return response.data
  }

  async deleteTerminology(id: string): Promise<void> {
    await this.client.delete(`/terminologies/${id}`)
  }

  // ===========================================================================
  // TERM ENDPOINTS
  // ===========================================================================

  async listTerms(terminologyId: string, params?: {
    status?: string
    search?: string
  }): Promise<TermListResponse> {
    const response = await this.client.get<TermListResponse>(
      `/terminologies/${terminologyId}/terms`,
      { params }
    )
    return response.data
  }

  async getTerm(termId: string): Promise<Term> {
    const response = await this.client.get<Term>(`/terms/${termId}`)
    return response.data
  }

  async createTerm(terminologyId: string, data: CreateTermRequest): Promise<Term> {
    const response = await this.client.post<Term>(
      `/terminologies/${terminologyId}/terms`,
      data
    )
    return response.data
  }

  async updateTerm(termId: string, data: UpdateTermRequest): Promise<Term> {
    const response = await this.client.put<Term>(`/terms/${termId}`, data)
    return response.data
  }

  async deprecateTerm(termId: string, data: DeprecateTermRequest): Promise<Term> {
    const response = await this.client.post<Term>(`/terms/${termId}/deprecate`, data)
    return response.data
  }

  async deleteTerm(termId: string): Promise<void> {
    await this.client.delete(`/terms/${termId}`)
  }

  async bulkCreateTerms(
    terminologyId: string,
    data: BulkCreateTermRequest
  ): Promise<BulkOperationResponse> {
    const response = await this.client.post<BulkOperationResponse>(
      `/terminologies/${terminologyId}/terms/bulk`,
      data
    )
    return response.data
  }

  // ===========================================================================
  // IMPORT/EXPORT ENDPOINTS
  // ===========================================================================

  async importTerminology(data: ImportTerminologyRequest): Promise<{
    terminology: Terminology
    terms_result: BulkOperationResponse
  }> {
    const response = await this.client.post('/import-export/import', data)
    return response.data
  }

  async exportTerminology(
    terminologyId: string,
    format: 'json' | 'csv' = 'json',
    includeInactive: boolean = false
  ): Promise<ExportTerminologyResponse | string> {
    const response = await this.client.get(`/import-export/export/${terminologyId}`, {
      params: { format, include_inactive: includeInactive }
    })
    return response.data
  }

  // ===========================================================================
  // VALIDATION ENDPOINTS
  // ===========================================================================

  async validateValue(data: ValidateValueRequest): Promise<ValidateValueResponse> {
    const response = await this.client.post<ValidateValueResponse>('/validation/validate', data)
    return response.data
  }

  async bulkValidate(data: BulkValidateRequest): Promise<BulkValidateResponse> {
    const response = await this.client.post<BulkValidateResponse>('/validation/validate-bulk', data)
    return response.data
  }
}

export const apiClient = new DefStoreClient()
export default apiClient
