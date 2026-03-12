/**
 * Error hierarchy for WIP client operations.
 *
 * Maps HTTP status codes and bulk response errors to typed exceptions.
 */

export class WipError extends Error {
  constructor(
    message: string,
    public readonly statusCode?: number,
    public readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'WipError'
  }
}

export class WipValidationError extends WipError {
  constructor(message: string, detail?: unknown) {
    super(message, 422, detail)
    this.name = 'WipValidationError'
  }
}

export class WipNotFoundError extends WipError {
  constructor(message: string, detail?: unknown) {
    super(message, 404, detail)
    this.name = 'WipNotFoundError'
  }
}

export class WipConflictError extends WipError {
  constructor(message: string, detail?: unknown) {
    super(message, 409, detail)
    this.name = 'WipConflictError'
  }
}

export class WipAuthError extends WipError {
  constructor(message: string, statusCode: number = 401, detail?: unknown) {
    super(message, statusCode, detail)
    this.name = 'WipAuthError'
  }
}

export class WipServerError extends WipError {
  constructor(message: string, statusCode: number = 500, detail?: unknown) {
    super(message, statusCode, detail)
    this.name = 'WipServerError'
  }
}

export class WipNetworkError extends WipError {
  constructor(message: string, public readonly cause?: Error) {
    super(message, undefined, undefined)
    this.name = 'WipNetworkError'
  }
}

/** Thrown by single-item convenience methods when the bulk response item has status "error". */
export class WipBulkItemError extends WipError {
  constructor(
    message: string,
    public readonly index: number,
    public readonly itemStatus: string,
  ) {
    super(message, undefined, undefined)
    this.name = 'WipBulkItemError'
  }
}
