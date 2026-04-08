'use strict';

// src/errors.ts
var WipError = class extends Error {
  constructor(message, statusCode, detail) {
    super(message);
    this.statusCode = statusCode;
    this.detail = detail;
    this.name = "WipError";
  }
};
var WipValidationError = class extends WipError {
  constructor(message, detail) {
    super(message, 422, detail);
    this.name = "WipValidationError";
  }
};
var WipNotFoundError = class extends WipError {
  constructor(message, detail) {
    super(message, 404, detail);
    this.name = "WipNotFoundError";
  }
};
var WipConflictError = class extends WipError {
  constructor(message, detail) {
    super(message, 409, detail);
    this.name = "WipConflictError";
  }
};
var WipAuthError = class extends WipError {
  constructor(message, statusCode = 401, detail) {
    super(message, statusCode, detail);
    this.name = "WipAuthError";
  }
};
var WipServerError = class extends WipError {
  constructor(message, statusCode = 500, detail) {
    super(message, statusCode, detail);
    this.name = "WipServerError";
  }
};
var WipNetworkError = class extends WipError {
  constructor(message, cause) {
    super(message, void 0, void 0);
    this.cause = cause;
    this.name = "WipNetworkError";
  }
};
var WipBulkItemError = class extends WipError {
  constructor(message, index, itemStatus, errorCode) {
    super(message, void 0, void 0);
    this.index = index;
    this.itemStatus = itemStatus;
    this.errorCode = errorCode;
    this.name = "WipBulkItemError";
  }
};

// src/http.ts
var DEFAULT_TIMEOUT = 3e4;
var DEFAULT_RETRY = { maxRetries: 2, baseDelayMs: 500, maxDelayMs: 5e3 };
var FetchTransport = class {
  constructor(config) {
    // Cache auth headers to avoid async overhead on synchronous providers
    this.cachedAuthHeaders = null;
    let baseUrl = config.baseUrl;
    if (!baseUrl || baseUrl.startsWith("/")) {
      if (typeof window !== "undefined" && window.location?.origin) {
        baseUrl = baseUrl ? window.location.origin + baseUrl : window.location.origin;
      } else if (!baseUrl) {
        throw new Error(
          'WipClient: baseUrl is required in non-browser environments. Pass an explicit baseUrl (e.g. "http://localhost:8001").'
        );
      } else {
        throw new Error(
          `WipClient: relative baseUrl "${baseUrl}" requires a browser environment. Pass an absolute URL (e.g. "http://localhost:8001") in Node.js.`
        );
      }
    }
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.auth = config.auth;
    this.timeout = config.timeout ?? DEFAULT_TIMEOUT;
    this.retry = config.retry ?? DEFAULT_RETRY;
    this.onAuthError = config.onAuthError;
  }
  setAuth(auth) {
    this.auth = auth;
    this.cachedAuthHeaders = null;
  }
  async request(method, path, options) {
    const url = this.buildUrl(path, options?.params);
    const headers = {
      ...options?.headers
    };
    if (this.auth) {
      if (!this.cachedAuthHeaders) {
        this.cachedAuthHeaders = await this.auth.getHeaders();
      }
      Object.assign(headers, this.cachedAuthHeaders);
    }
    if (options?.body !== void 0 && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const fetchOptions = {
      method,
      headers,
      // Enable HTTP keep-alive for connection reuse across batches
      keepalive: true
    };
    if (options?.body !== void 0) {
      fetchOptions.body = options.body instanceof FormData ? options.body : JSON.stringify(options.body);
    }
    const isRetryable = method === "GET";
    const maxAttempts = isRetryable ? this.retry.maxRetries + 1 : 1;
    let lastError;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0) {
        const delay = Math.min(
          (this.retry.baseDelayMs ?? 500) * 2 ** (attempt - 1),
          this.retry.maxDelayMs ?? 5e3
        );
        await new Promise((r) => setTimeout(r, delay));
      }
      const controller = new AbortController();
      const timeoutMs = options?.timeout ?? this.timeout;
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      fetchOptions.signal = controller.signal;
      try {
        const response = await fetch(url, fetchOptions);
        clearTimeout(timeoutId);
        if (!response.ok) {
          const error = await this.mapResponseError(response);
          if (error instanceof WipAuthError) {
            this.cachedAuthHeaders = null;
            if (this.onAuthError) this.onAuthError();
          }
          if (isRetryable && attempt < maxAttempts - 1 && response.status >= 502) {
            lastError = error;
            continue;
          }
          throw error;
        }
        const responseType = options?.responseType ?? "json";
        if (responseType === "blob") {
          return await response.blob();
        }
        if (responseType === "text") {
          return await response.text();
        }
        if (response.status === 204) {
          return void 0;
        }
        try {
          return await response.json();
        } catch {
          return void 0;
        }
      } catch (err) {
        clearTimeout(timeoutId);
        if (err instanceof WipError) {
          if (isRetryable && attempt < maxAttempts - 1 && err instanceof WipServerError) {
            lastError = err;
            continue;
          }
          throw err;
        }
        if (err instanceof DOMException && err.name === "AbortError") {
          lastError = new WipNetworkError(`Request timed out after ${timeoutMs}ms`);
          if (attempt < maxAttempts - 1) continue;
          throw lastError;
        }
        lastError = new WipNetworkError(
          err instanceof Error ? err.message : "Network error",
          err instanceof Error ? err : void 0
        );
        if (attempt < maxAttempts - 1) continue;
        throw lastError;
      }
    }
    throw lastError ?? new WipNetworkError("Request failed after all retries");
  }
  buildUrl(path, params) {
    const fullUrl = `${this.baseUrl}${path.startsWith("/") ? path : "/" + path}`;
    if (!params) return fullUrl;
    const parsed = new URL(fullUrl);
    for (const [key, value] of Object.entries(params)) {
      if (value === void 0 || value === null) continue;
      if (Array.isArray(value)) {
        for (const v of value) {
          parsed.searchParams.append(key, String(v));
        }
      } else if (typeof value === "boolean") {
        parsed.searchParams.set(key, value ? "true" : "false");
      } else {
        parsed.searchParams.set(key, String(value));
      }
    }
    return parsed.toString();
  }
  async mapResponseError(response) {
    let detail;
    try {
      const text = await response.text();
      detail = text ? JSON.parse(text) : void 0;
    } catch {
      detail = void 0;
    }
    const message = this.extractMessage(detail, response.statusText);
    switch (response.status) {
      case 400:
        return new WipValidationError(message, detail);
      case 401:
      case 403:
        return new WipAuthError(message, response.status, detail);
      case 404:
        return new WipNotFoundError(message, detail);
      case 409:
        return new WipConflictError(message, detail);
      case 422:
        return new WipValidationError(message, detail);
      default:
        if (response.status >= 500) {
          return new WipServerError(message, response.status, detail);
        }
        return new WipError(message, response.status, detail);
    }
  }
  extractMessage(detail, fallback) {
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const d = detail;
      if (typeof d.detail === "string") return d.detail;
      if (typeof d.detail === "object" && d.detail !== null) {
        const inner = d.detail;
        return String(inner.message || inner.error || JSON.stringify(d.detail));
      }
      if (typeof d.message === "string") return d.message;
      if (typeof d.error === "string") return d.error;
    }
    return fallback;
  }
};

// src/auth/api-key.ts
var ApiKeyAuthProvider = class {
  constructor(apiKey) {
    this.apiKey = apiKey;
  }
  getHeaders() {
    return { "X-API-Key": this.apiKey };
  }
  setApiKey(key) {
    this.apiKey = key;
  }
};

// src/auth/oidc.ts
var OidcAuthProvider = class {
  constructor(getToken) {
    this.getToken = getToken;
  }
  async getHeaders() {
    const token = await this.getToken();
    return { Authorization: `Bearer ${token}` };
  }
};

// src/services/base.ts
var BaseService = class {
  constructor(transport, basePath) {
    this.transport = transport;
    this.basePath = basePath;
  }
  get(path, params) {
    return this.transport.request("GET", `${this.basePath}${path}`, {
      params
    });
  }
  post(path, body, params) {
    return this.transport.request("POST", `${this.basePath}${path}`, {
      body,
      params
    });
  }
  put(path, body, params) {
    return this.transport.request("PUT", `${this.basePath}${path}`, {
      body,
      params
    });
  }
  patch(path, body, params) {
    return this.transport.request("PATCH", `${this.basePath}${path}`, {
      body,
      params
    });
  }
  del(path, body, params) {
    return this.transport.request("DELETE", `${this.basePath}${path}`, {
      body,
      params
    });
  }
  getBlob(path, params) {
    return this.transport.request("GET", `${this.basePath}${path}`, {
      params,
      responseType: "blob"
    });
  }
  postFormData(path, formData, params) {
    return this.transport.request("POST", `${this.basePath}${path}`, {
      body: formData,
      params
    });
  }
  bulkWrite(path, items, method = "POST") {
    return this.transport.request(method, `${this.basePath}${path}`, {
      body: items
    });
  }
  async bulkWriteOne(path, item, method = "POST") {
    const resp = await this.bulkWrite(path, [item], method);
    const result = resp.results[0];
    if (result.status === "error") {
      throw new WipBulkItemError(
        result.error || "Operation failed",
        result.index,
        result.status,
        result.error_code
      );
    }
    return result;
  }
};

// src/services/def-store.ts
var DefStoreService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/def-store");
  }
  // ---- Terminologies ----
  async listTerminologies(params) {
    return this.get("/terminologies", params);
  }
  async getTerminology(id) {
    return this.get(`/terminologies/${id}`);
  }
  async createTerminology(data) {
    return this.bulkWriteOne("/terminologies", data);
  }
  async createTerminologies(data) {
    return this.bulkWrite("/terminologies", data);
  }
  async updateTerminology(id, data) {
    return this.bulkWriteOne("/terminologies", { ...data, terminology_id: id }, "PUT");
  }
  async deleteTerminology(id, options) {
    return this.bulkWriteOne("/terminologies", {
      id,
      force: options?.force,
      hard_delete: options?.hardDelete
    }, "DELETE");
  }
  // ---- Terms ----
  async listTerms(terminologyId, params) {
    return this.get(`/terminologies/${terminologyId}/terms`, params);
  }
  async getTerm(termId) {
    return this.get(`/terms/${termId}`);
  }
  async createTerm(terminologyId, data, options) {
    const resp = await this.post(
      `/terminologies/${terminologyId}/terms`,
      [data],
      { namespace: options.namespace }
    );
    const result = resp.results[0];
    if (result.status === "error") {
      throw new WipBulkItemError(
        result.error || "Operation failed",
        result.index,
        result.status
      );
    }
    return result;
  }
  async createTerms(terminologyId, terms, options) {
    return this.post(`/terminologies/${terminologyId}/terms`, terms, options);
  }
  async updateTerm(termId, data) {
    return this.bulkWriteOne("/terms", { ...data, term_id: termId }, "PUT");
  }
  async deprecateTerm(termId, data) {
    return this.bulkWriteOne("/terms/deprecate", { ...data, term_id: termId });
  }
  async deleteTerm(termId, options) {
    return this.bulkWriteOne("/terms", {
      id: termId,
      hard_delete: options?.hardDelete
    }, "DELETE");
  }
  // ---- Import/Export ----
  async importTerminology(data) {
    return this.post("/import-export/import", data);
  }
  async exportTerminology(terminologyId, options) {
    return this.get(`/import-export/export/${terminologyId}`, {
      format: options?.format ?? "json",
      include_inactive: options?.includeInactive,
      include_relationships: options?.includeRelationships,
      include_metadata: options?.includeMetadata,
      languages: options?.languages
    });
  }
  async importOntology(data, options) {
    return this.post("/import-export/import-ontology", data, options);
  }
  // ---- Validation ----
  async validateValue(data) {
    return this.post("/validate", data);
  }
  async bulkValidate(data) {
    return this.post("/validate/bulk", data);
  }
  // ---- Ontology / Relationships ----
  async listRelationships(params) {
    return this.get("/ontology/relationships", params);
  }
  async listAllRelationships(params) {
    return this.get("/ontology/relationships/all", params);
  }
  async createRelationships(items, namespace) {
    return this.post("/ontology/relationships", items, { namespace });
  }
  async deleteRelationships(items, namespace) {
    return this.del("/ontology/relationships", items, { namespace });
  }
  async getAncestors(termId, params) {
    return this.get(`/ontology/terms/${termId}/ancestors`, params);
  }
  async getDescendants(termId, params) {
    return this.get(`/ontology/terms/${termId}/descendants`, params);
  }
  async getParents(termId, namespace) {
    return this.get(`/ontology/terms/${termId}/parents`, { namespace });
  }
  async getChildren(termId, namespace) {
    return this.get(`/ontology/terms/${termId}/children`, { namespace });
  }
  // ---- Audit Log ----
  async getTerminologyAuditLog(terminologyId, params) {
    return this.get(`/audit/terminologies/${terminologyId}`, params);
  }
  async getTermAuditLog(termId, params) {
    return this.get(`/audit/terms/${termId}`, params);
  }
  async getRecentAuditLog(params) {
    return this.get("/audit/", params);
  }
};

// src/services/template-store.ts
var TemplateStoreService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/template-store");
  }
  // ---- Templates ----
  async listTemplates(params) {
    return this.get("/templates", params);
  }
  async getTemplate(id, version) {
    return this.get(`/templates/${id}`, version ? { version } : void 0);
  }
  async getTemplateRaw(id, version) {
    return this.get(`/templates/${id}/raw`, version ? { version } : void 0);
  }
  async getTemplateByValue(value) {
    return this.get(`/templates/by-value/${value}`);
  }
  async getTemplateByValueRaw(value, namespace) {
    return this.get(`/templates/by-value/${value}/raw?namespace=${encodeURIComponent(namespace)}`);
  }
  async getTemplateVersions(value) {
    return this.get(`/templates/by-value/${value}/versions`);
  }
  async getTemplateByValueAndVersion(value, version) {
    return this.get(`/templates/by-value/${value}/versions/${version}`);
  }
  async createTemplate(data) {
    return this.bulkWriteOne("/templates", data);
  }
  async createTemplates(data) {
    return this.bulkWrite("/templates", data);
  }
  async updateTemplate(id, data) {
    return this.bulkWriteOne("/templates", { ...data, template_id: id }, "PUT");
  }
  async deleteTemplate(id, options) {
    return this.bulkWriteOne("/templates", {
      id,
      version: options?.version,
      force: options?.force,
      hard_delete: options?.hardDelete,
      updated_by: options?.updatedBy
    }, "DELETE");
  }
  async validateTemplate(id, request = {}) {
    return this.post(`/templates/${id}/validate`, request);
  }
  // ---- Inheritance ----
  async getChildren(id) {
    return this.get(`/templates/${id}/children`);
  }
  async getDescendants(id) {
    return this.get(`/templates/${id}/descendants`);
  }
  // ---- Draft Mode ----
  async activateTemplate(id, options) {
    return this.post(`/templates/${id}/activate`, null, options);
  }
  // ---- Cascade ----
  async cascadeTemplate(id) {
    return this.post(`/templates/${id}/cascade`);
  }
};

// src/services/document-store.ts
var DocumentStoreService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/document-store");
  }
  // ---- Documents ----
  async listDocuments(params) {
    return this.get("/documents", params);
  }
  async getDocument(id, version) {
    return this.get(`/documents/${id}`, version !== void 0 ? { version } : void 0);
  }
  async createDocument(data) {
    return this.bulkWriteOne("/documents", data);
  }
  async createDocuments(data) {
    return this.bulkWrite("/documents", data);
  }
  /**
   * Apply an RFC 7396 JSON Merge Patch to a document.
   *
   * Single-item convenience that wraps the bulk PATCH endpoint and unwraps the
   * single result. Throws {@link WipBulkItemError} (with `errorCode` populated)
   * on a per-item failure (e.g. `not_found`, `validation_failed`,
   * `concurrency_conflict`, `identity_field_change`).
   *
   * Identity fields cannot be changed via PATCH — use {@link createDocument}
   * to create a new document instead.
   */
  async updateDocument(documentId, patch, options) {
    const item = { document_id: documentId, patch };
    if (options?.ifMatch !== void 0) {
      item.if_match = options.ifMatch;
    }
    return this.bulkWriteOne("/documents", item, "PATCH");
  }
  /**
   * Bulk PATCH /documents — apply RFC 7396 merge patches to multiple documents
   * in a single round-trip. Each item is processed independently; per-item
   * failures appear in the response with a populated `error_code`.
   */
  async updateDocuments(items) {
    return this.bulkWrite("/documents", items, "PATCH");
  }
  async deleteDocument(id, options) {
    return this.bulkWriteOne("/documents", {
      id,
      updated_by: options?.updatedBy,
      hard_delete: options?.hardDelete,
      version: options?.version
    }, "DELETE");
  }
  async archiveDocument(id, archivedBy) {
    return this.bulkWriteOne("/documents/archive", { id, archived_by: archivedBy });
  }
  // NOTE: restoreDocument was removed — no /documents/{id}/restore endpoint exists in the API spec.
  // ---- Validation ----
  async validateDocument(data) {
    return this.post("/validation/validate", data);
  }
  // ---- Versions ----
  async getVersions(id) {
    return this.get(`/documents/${id}/versions`);
  }
  async getVersion(id, version) {
    return this.get(`/documents/${id}/versions/${version}`);
  }
  // ---- Table View ----
  async getTableView(templateId, params) {
    return this.get(`/table/${templateId}`, params);
  }
  async exportTableCsv(templateId, params) {
    return this.getBlob(`/table/${templateId}/csv`, params);
  }
  // ---- Additional Lookups ----
  async getLatestDocument(id) {
    return this.get(`/documents/${id}/latest`);
  }
  async getDocumentByIdentity(identityHash, includeInactive) {
    return this.get(`/documents/by-identity/${identityHash}`, includeInactive !== void 0 ? { include_inactive: includeInactive } : void 0);
  }
  async queryDocuments(body) {
    return this.post("/documents/query", body);
  }
  // ---- Import ----
  async previewImport(file, filename) {
    const form = new FormData();
    form.append("file", file, filename);
    return this.postFormData("/import/preview", form);
  }
  async importDocuments(file, filename, options) {
    const form = new FormData();
    form.append("file", file, filename);
    form.append("template_id", options.template_id);
    form.append("column_mapping", JSON.stringify(options.column_mapping));
    form.append("namespace", options.namespace);
    if (options.skip_errors) {
      form.append("skip_errors", "true");
    }
    return this.postFormData("/import", form);
  }
  // ---- Replay ----
  async startReplay(request = {}) {
    return this.post("/replay/start", request);
  }
  async getReplayStatus(sessionId) {
    return this.get(`/replay/${sessionId}`);
  }
  async pauseReplay(sessionId) {
    return this.post(`/replay/${sessionId}/pause`);
  }
  async resumeReplay(sessionId) {
    return this.post(`/replay/${sessionId}/resume`);
  }
  async cancelReplay(sessionId) {
    return this.del(`/replay/${sessionId}`);
  }
};

// src/services/file-store.ts
var FileStoreService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/document-store/files");
  }
  // ---- Files ----
  async uploadFile(file, filename, metadata) {
    const formData = new FormData();
    if (file instanceof File) {
      formData.append("file", file);
    } else {
      formData.append("file", file, filename ?? "upload");
    }
    if (metadata?.description) formData.append("description", metadata.description);
    if (metadata?.tags?.length) formData.append("tags", metadata.tags.join(","));
    if (metadata?.category) formData.append("category", metadata.category);
    if (metadata?.allowed_templates?.length) {
      formData.append("allowed_templates", metadata.allowed_templates.join(","));
    }
    return this.postFormData("", formData);
  }
  async listFiles(params) {
    return this.get("", params);
  }
  async getFile(fileId) {
    return this.get(`/${fileId}`);
  }
  async getDownloadUrl(fileId, expiresIn) {
    return this.get(`/${fileId}/download`, expiresIn ? { expires_in: expiresIn } : void 0);
  }
  async downloadFileContent(fileId) {
    return this.getBlob(`/${fileId}/content`);
  }
  async updateMetadata(fileId, data) {
    return this.bulkWriteOne("", { ...data, file_id: fileId }, "PATCH");
  }
  async deleteFile(fileId) {
    return this.bulkWriteOne("", { id: fileId }, "DELETE");
  }
  async deleteFiles(fileIds) {
    return this.bulkWrite("", fileIds.map((id) => ({ id })), "DELETE");
  }
  async hardDeleteFile(fileId) {
    return this.del(`/${fileId}/hard`);
  }
  // ---- Utility ----
  async listOrphans(params) {
    return this.get("/orphans/list", params);
  }
  async findByChecksum(checksum) {
    return this.get(`/by-checksum/${checksum}`);
  }
  async checkIntegrity() {
    return this.get("/health/integrity");
  }
  async getFileDocuments(fileId, page = 1, pageSize = 10) {
    return this.get(`/${fileId}/documents`, { page, page_size: pageSize });
  }
};

// src/services/registry.ts
var RegistryService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/registry");
  }
  // ---- Namespaces ----
  async listNamespaces(includeArchived = false) {
    return this.get("/namespaces", { include_archived: includeArchived });
  }
  async getNamespace(prefix) {
    return this.get(`/namespaces/${prefix}`);
  }
  async getNamespaceStats(prefix) {
    return this.get(`/namespaces/${prefix}/stats`);
  }
  async createNamespace(data) {
    return this.post("/namespaces", data);
  }
  async updateNamespace(prefix, data) {
    return this.put(`/namespaces/${prefix}`, data);
  }
  async archiveNamespace(prefix, archivedBy) {
    return this.post(`/namespaces/${prefix}/archive`, null, archivedBy ? { archived_by: archivedBy } : void 0);
  }
  async restoreNamespace(prefix, restoredBy) {
    return this.post(`/namespaces/${prefix}/restore`, null, restoredBy ? { restored_by: restoredBy } : void 0);
  }
  async deleteNamespace(prefix, deletedBy) {
    return this.del(`/namespaces/${prefix}`, void 0, { confirm: true, deleted_by: deletedBy });
  }
  async initializeWipNamespace() {
    return this.post("/namespaces/initialize-wip");
  }
  // ---- Entries ----
  async listEntries(params) {
    return this.get("/entries", params);
  }
  async lookupEntry(entryId) {
    const resp = await this.post(
      "/entries/lookup/by-id",
      [{ entry_id: entryId }]
    );
    return resp.results[0];
  }
  async searchEntries(term, options) {
    const resp = await this.post(
      "/entries/search/by-term",
      [{
        term,
        restrict_to_namespaces: options?.namespaces,
        restrict_to_entity_types: options?.entityTypes,
        include_inactive: options?.includeInactive ?? false
      }]
    );
    return resp.results[0]?.results ?? [];
  }
  async unifiedSearch(params) {
    return this.get("/entries/search", params);
  }
  async getEntry(entryId) {
    return this.get(`/entries/${entryId}`);
  }
  // ---- Mutations ----
  async addSynonym(request) {
    const resp = await this.post(
      "/synonyms/add",
      [request]
    );
    return resp.results[0];
  }
  async removeSynonym(request) {
    const resp = await this.post(
      "/synonyms/remove",
      [request]
    );
    return resp.results[0];
  }
  async mergeEntries(request) {
    const resp = await this.post(
      "/synonyms/merge",
      [request]
    );
    return resp.results[0];
  }
  async deactivateEntry(entryId, updatedBy) {
    const resp = await this.del(
      "/entries",
      [{ entry_id: entryId, updated_by: updatedBy }]
    );
    return resp.results[0];
  }
  // ---- Namespace Export/Import ----
  async exportNamespace(prefix, options) {
    return this.post(`/namespaces/${prefix}/export`, null, options);
  }
  async downloadExport(exportId) {
    return this.getBlob(`/namespaces/exports/${exportId}`);
  }
  async importNamespace(file, options) {
    const form = new FormData();
    form.append("file", file);
    return this.postFormData("/namespaces/import", form, options);
  }
  // ---- Grants ----
  async listGrants(prefix) {
    return this.get(`/namespaces/${prefix}/grants`);
  }
  async createGrants(prefix, grants) {
    return this.post(`/namespaces/${prefix}/grants`, grants);
  }
  async revokeGrants(prefix, grants) {
    return this.del(`/namespaces/${prefix}/grants`, grants);
  }
  // ---- API Keys ----
  async listAPIKeys() {
    return this.get("/api-keys");
  }
  async createAPIKey(request) {
    return this.post("/api-keys", request);
  }
  async getAPIKey(name) {
    return this.get(`/api-keys/${name}`);
  }
  async updateAPIKey(name, request) {
    return this.patch(`/api-keys/${name}`, request);
  }
  async revokeAPIKey(name) {
    return this.del(`/api-keys/${name}`);
  }
};

// src/services/reporting-sync.ts
var ReportingSyncService = class extends BaseService {
  constructor(transport) {
    super(transport, "/api/reporting-sync");
  }
  // ── Health & Status ──
  async healthCheck() {
    try {
      await this.transport.request("GET", "/health");
      return true;
    } catch {
      return false;
    }
  }
  async getSyncStatus() {
    return this.get("/status");
  }
  // ── SQL Query Execution ──
  /** Execute a read-only SQL query against the PostgreSQL reporting database */
  async runQuery(sql, params, options) {
    const body = {
      sql,
      params: params || [],
      ...options
    };
    return this.post("/query", body);
  }
  // ── Sync Awareness ──
  /**
   * Wait for the reporting sync to catch up.
   *
   * Simple form — waits until at least one new event is processed:
   *   await client.reporting.awaitSync()
   *
   * Query form — waits until a specific row exists in PostgreSQL:
   *   await client.reporting.awaitSync({
   *     query: "SELECT 1 FROM dnd_monster WHERE document_id = $1",
   *     params: [docId],
   *   })
   */
  async awaitSync(options) {
    const timeout = options?.timeout ?? 5e3;
    const interval = options?.interval ?? 200;
    const deadline = Date.now() + timeout;
    if (options?.query) {
      while (Date.now() < deadline) {
        const result = await this.runQuery(options.query, options.params, { max_rows: 1 });
        if (result.row_count > 0) return;
        await new Promise((resolve) => setTimeout(resolve, interval));
      }
      throw new WipError("Sync timeout: expected data not found in PostgreSQL");
    }
    const before = await this.getSyncStatus();
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, interval));
      const current = await this.getSyncStatus();
      if (current.events_processed > before.events_processed) return;
    }
    throw new WipError("Sync timeout: no new events processed");
  }
  // ── Table Introspection ──
  /** List all PostgreSQL reporting tables */
  async listTables(tableName) {
    return this.get("/tables", tableName ? { table_name: tableName } : void 0);
  }
  /** Get PostgreSQL schema for a template's reporting table */
  async getTableSchema(templateValue) {
    return this.get(`/schema/${templateValue}`);
  }
  // ── Integrity ──
  async getIntegrityCheck(params) {
    return this.get("/health/integrity", params);
  }
  // ── Search & Activity ──
  async search(params) {
    return this.post("/search", params);
  }
  async getRecentActivity(params) {
    return this.get("/activity/recent", params);
  }
  // ── References ──
  async getTermDocuments(termId, limit) {
    return this.get(`/references/term/${termId}/documents`, limit ? { limit } : void 0);
  }
  async getEntityReferences(entityType, entityId) {
    return this.get(`/entity/${entityType}/${entityId}/references`);
  }
  async getReferencedBy(entityType, entityId, limit = 100) {
    return this.get(`/entity/${entityType}/${entityId}/referenced-by`, { limit });
  }
};

// src/client.ts
function resolveAuth(auth) {
  if (!auth) return void 0;
  if ("getHeaders" in auth) return auth;
  if (auth.type === "api-key") return new ApiKeyAuthProvider(auth.key);
  if (auth.type === "oidc") return new OidcAuthProvider(auth.getToken);
  return void 0;
}
function createWipClient(config) {
  const transport = new FetchTransport({
    baseUrl: config.baseUrl,
    auth: resolveAuth(config.auth),
    timeout: config.timeout,
    retry: config.retry,
    onAuthError: config.onAuthError
  });
  return {
    defStore: new DefStoreService(transport),
    templates: new TemplateStoreService(transport),
    documents: new DocumentStoreService(transport),
    files: new FileStoreService(transport),
    registry: new RegistryService(transport),
    reporting: new ReportingSyncService(transport),
    setAuth(auth) {
      transport.setAuth(auth);
    }
  };
}

// src/utils/query-string.ts
function buildQueryString(params) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === void 0 || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) {
        searchParams.append(key, String(v));
      }
    } else if (typeof value === "boolean") {
      searchParams.set(key, value ? "true" : "false");
    } else {
      searchParams.set(key, String(value));
    }
  }
  const str = searchParams.toString();
  return str ? `?${str}` : "";
}

// src/utils/template-to-form.ts
var FIELD_TYPE_TO_INPUT = {
  string: "text",
  number: "number",
  integer: "integer",
  boolean: "checkbox",
  date: "date",
  datetime: "datetime",
  term: "select",
  reference: "search",
  file: "file",
  object: "group",
  array: "list"
};
function fieldToFormField(field, identityFields) {
  const inputType = FIELD_TYPE_TO_INPUT[field.type] ?? "text";
  const formField = {
    name: field.name,
    label: field.label,
    inputType,
    required: field.mandatory,
    isIdentity: identityFields.includes(field.name)
  };
  if (field.default_value !== void 0) formField.defaultValue = field.default_value;
  if (field.terminology_ref) formField.terminologyCode = field.terminology_ref;
  if (field.reference_type) formField.referenceType = field.reference_type;
  if (field.target_templates?.length) formField.targetTemplates = field.target_templates;
  if (field.target_terminologies?.length) formField.targetTerminologies = field.target_terminologies;
  if (field.semantic_type) formField.semanticType = field.semantic_type;
  if (field.file_config) {
    formField.fileConfig = {
      allowedTypes: field.file_config.allowed_types,
      maxSizeMb: field.file_config.max_size_mb,
      multiple: field.file_config.multiple,
      maxFiles: field.file_config.max_files
    };
  }
  if (field.array_item_type) {
    formField.arrayItemType = FIELD_TYPE_TO_INPUT[field.array_item_type] ?? "text";
  }
  if (field.array_terminology_ref) formField.arrayTerminologyCode = field.array_terminology_ref;
  if (field.validation) {
    formField.validation = {
      pattern: field.validation.pattern,
      minLength: field.validation.min_length,
      maxLength: field.validation.max_length,
      minimum: field.validation.minimum,
      maximum: field.validation.maximum,
      enum: field.validation.enum
    };
  }
  return formField;
}
function templateToFormSchema(template) {
  return template.fields.map((f) => fieldToFormField(f, template.identity_fields));
}

// src/utils/bulk-import.ts
async function bulkImport(items, writeFn, options) {
  const batchSize = options?.batchSize ?? 100;
  const concurrency = options?.concurrency ?? 1;
  const continueOnError = options?.continueOnError ?? true;
  const progress = {
    processed: 0,
    total: items.length,
    succeeded: 0,
    failed: 0
  };
  const batches = [];
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push(items.slice(i, i + batchSize));
  }
  if (concurrency <= 1) {
    for (const batch of batches) {
      const result = await writeFn(batch);
      progress.processed += batch.length;
      progress.succeeded += result.succeeded;
      progress.failed += result.failed;
      options?.onProgress?.({ ...progress });
      if (!continueOnError && result.failed > 0) {
        break;
      }
    }
  } else {
    let stopped = false;
    let batchIndex = 0;
    while (batchIndex < batches.length && !stopped) {
      const chunk = batches.slice(batchIndex, batchIndex + concurrency);
      batchIndex += chunk.length;
      const results = await Promise.all(
        chunk.map(async (batch) => {
          const result = await writeFn(batch);
          return { batch, result };
        })
      );
      for (const { batch, result } of results) {
        progress.processed += batch.length;
        progress.succeeded += result.succeeded;
        progress.failed += result.failed;
        if (!continueOnError && result.failed > 0) {
          stopped = true;
        }
      }
      options?.onProgress?.({ ...progress });
    }
  }
  return progress;
}

// src/utils/resolve-reference.ts
async function resolveReference(client, templateId, searchTerm, limit = 10) {
  const docs = await client.documents.queryDocuments({
    template_id: templateId,
    page_size: 100,
    status: "active",
    sort_by: "created_at",
    sort_order: "desc"
  });
  const term = searchTerm.toLowerCase();
  const matched = docs.items.filter(
    (doc) => Object.values(doc.data).some(
      (v) => typeof v === "string" && v.toLowerCase().includes(term)
    )
  ).slice(0, limit);
  return matched.map((doc) => ({
    documentId: doc.document_id,
    displayValue: Object.values(doc.data).filter((v) => typeof v === "string").join(" - ") || doc.document_id,
    identityFields: doc.data
  }));
}

exports.ApiKeyAuthProvider = ApiKeyAuthProvider;
exports.DefStoreService = DefStoreService;
exports.DocumentStoreService = DocumentStoreService;
exports.FetchTransport = FetchTransport;
exports.FileStoreService = FileStoreService;
exports.OidcAuthProvider = OidcAuthProvider;
exports.RegistryService = RegistryService;
exports.ReportingSyncService = ReportingSyncService;
exports.TemplateStoreService = TemplateStoreService;
exports.WipAuthError = WipAuthError;
exports.WipBulkItemError = WipBulkItemError;
exports.WipConflictError = WipConflictError;
exports.WipError = WipError;
exports.WipNetworkError = WipNetworkError;
exports.WipNotFoundError = WipNotFoundError;
exports.WipServerError = WipServerError;
exports.WipValidationError = WipValidationError;
exports.buildQueryString = buildQueryString;
exports.bulkImport = bulkImport;
exports.createWipClient = createWipClient;
exports.resolveReference = resolveReference;
exports.templateToFormSchema = templateToFormSchema;
//# sourceMappingURL=index.cjs.map
//# sourceMappingURL=index.cjs.map