import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import {
  APP_SESSION_MESSAGE,
  APP_SESSION_RENEW_PATH,
  useAppSessionRenewal,
} from '../src/app-session'

function frames(): HTMLIFrameElement[] {
  return Array.from(document.querySelectorAll('iframe'))
}

function answer(data: Record<string, unknown>, origin = window.location.origin) {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: APP_SESSION_MESSAGE, ...data }, origin }),
    )
  })
}

describe('useAppSessionRenewal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('renews in a hidden iframe before the session expires, then again', () => {
    renderHook(() => useAppSessionRenewal({ intervalMs: 60_000 }))
    expect(frames()).toHaveLength(0)

    act(() => { vi.advanceTimersByTime(60_000) })
    expect(frames()).toHaveLength(1)
    expect(frames()[0].getAttribute('src')).toBe(APP_SESSION_RENEW_PATH)
    expect(frames()[0].style.display).toBe('none')

    answer({ ok: true, expires_in: 40, reason: '' })
    expect(frames()).toHaveLength(0)
    // Half the remaining lifetime (20 s) comes before the interval.
    act(() => { vi.advanceTimersByTime(20_000) })
    expect(frames()).toHaveLength(1)
  })

  it('runs onSessionLost when the WIP login has ended', () => {
    const lost = vi.fn()
    renderHook(() => useAppSessionRenewal({ intervalMs: 60_000, onSessionLost: lost }))
    act(() => { vi.advanceTimersByTime(60_000) })
    answer({ ok: false, expires_in: 0, reason: 'login_required' })
    expect(lost).toHaveBeenCalledWith('login_required')
    act(() => { vi.advanceTimersByTime(10 * 60_000) })
    expect(frames()).toHaveLength(0) // stopped
  })

  it('stops when the page is not on an app origin', () => {
    renderHook(() => useAppSessionRenewal({ intervalMs: 60_000 }))
    act(() => { vi.advanceTimersByTime(60_000) })
    answer({ ok: true, expires_in: 0, reason: 'not_an_app_origin' })
    act(() => { vi.advanceTimersByTime(10 * 60_000) })
    expect(frames()).toHaveLength(0)
  })

  it('never reloads when nothing answers; it tries again later', () => {
    const lost = vi.fn()
    renderHook(() =>
      useAppSessionRenewal({ intervalMs: 60_000, timeoutMs: 5_000, onSessionLost: lost }),
    )
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(frames()).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(5_000) })
    expect(frames()).toHaveLength(0)
    expect(lost).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(frames()).toHaveLength(1)
  })

  it('ignores messages from another origin', () => {
    const lost = vi.fn()
    renderHook(() => useAppSessionRenewal({ intervalMs: 60_000, onSessionLost: lost }))
    act(() => { vi.advanceTimersByTime(60_000) })
    answer({ ok: false, expires_in: 0, reason: 'login_required' }, 'https://evil.example')
    expect(lost).not.toHaveBeenCalled()
    expect(frames()).toHaveLength(1)
  })

  it('does nothing when turned off, and cleans up on unmount', () => {
    renderHook(() => useAppSessionRenewal(false))
    act(() => { vi.advanceTimersByTime(10 * 60_000) })
    expect(frames()).toHaveLength(0)

    const { unmount } = renderHook(() => useAppSessionRenewal({ intervalMs: 60_000 }))
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(frames()).toHaveLength(1)
    unmount()
    expect(frames()).toHaveLength(0)
  })
})
