import { describe, it, expect } from 'vitest'
import {
  WipError,
  WipValidationError,
  WipNotFoundError,
  WipConflictError,
  WipAuthError,
  WipServerError,
  WipNetworkError,
  WipBulkItemError,
} from '../src/errors'

describe('WipError hierarchy', () => {
  it('WipError has correct properties', () => {
    const err = new WipError('test', 500, { foo: 'bar' })
    expect(err.message).toBe('test')
    expect(err.statusCode).toBe(500)
    expect(err.detail).toEqual({ foo: 'bar' })
    expect(err.name).toBe('WipError')
    expect(err).toBeInstanceOf(Error)
  })

  it('WipValidationError defaults to 422', () => {
    const err = new WipValidationError('bad input')
    expect(err.statusCode).toBe(422)
    expect(err.name).toBe('WipValidationError')
    expect(err).toBeInstanceOf(WipError)
  })

  it('WipNotFoundError defaults to 404', () => {
    const err = new WipNotFoundError('not found')
    expect(err.statusCode).toBe(404)
    expect(err.name).toBe('WipNotFoundError')
  })

  it('WipConflictError defaults to 409', () => {
    const err = new WipConflictError('conflict')
    expect(err.statusCode).toBe(409)
  })

  it('WipAuthError defaults to 401', () => {
    const err = new WipAuthError('unauthorized')
    expect(err.statusCode).toBe(401)
    expect(err.name).toBe('WipAuthError')
  })

  it('WipAuthError accepts custom status (403)', () => {
    const err = new WipAuthError('forbidden', 403)
    expect(err.statusCode).toBe(403)
  })

  it('WipServerError defaults to 500', () => {
    const err = new WipServerError('internal error')
    expect(err.statusCode).toBe(500)
    expect(err.name).toBe('WipServerError')
  })

  it('WipNetworkError has no status code', () => {
    const err = new WipNetworkError('network down')
    expect(err.statusCode).toBeUndefined()
    expect(err.name).toBe('WipNetworkError')
  })

  it('WipBulkItemError carries index and itemStatus', () => {
    const err = new WipBulkItemError('duplicate', 2, 'error')
    expect(err.index).toBe(2)
    expect(err.itemStatus).toBe('error')
    expect(err.name).toBe('WipBulkItemError')
    expect(err).toBeInstanceOf(WipError)
  })
})
