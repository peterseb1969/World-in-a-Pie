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

describe('the default handler never reloads in a loop (CASE-1105)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.sessionStorage.clear()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    window.sessionStorage.clear()
  })

  function failOnce() {
    const { unmount } = renderHook(() => useAppSessionRenewal({ intervalMs: 60_000 }))
    act(() => { vi.advanceTimersByTime(60_000) })
    answer({ ok: false, expires_in: 0, reason: 'login_required' })
    unmount()
  }

  it('reloads once, then warns instead of reloading again', () => {
    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload } as Location)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    failOnce()
    expect(reload).toHaveBeenCalledTimes(1)
    // "After the reload": the first renewal fails again within two intervals.
    failOnce()
    expect(reload).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('different sites'))
  })

  it('a successful renewal clears the guard, so a later logout reloads again', () => {
    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload } as Location)

    failOnce()
    const { unmount } = renderHook(() => useAppSessionRenewal({ intervalMs: 60_000 }))
    act(() => { vi.advanceTimersByTime(60_000) })
    answer({ ok: true, expires_in: 900, reason: '' })
    unmount()
    failOnce()
    expect(reload).toHaveBeenCalledTimes(2)
  })
})
