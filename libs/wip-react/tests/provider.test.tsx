import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WipProvider, useWipClient } from '../src/provider'
import type { WipClient } from '@wip/client'
import type { ReactNode } from 'react'

function createWrapper(client: WipClient) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <WipProvider client={client}>
          {children}
        </WipProvider>
      </QueryClientProvider>
    )
  }
}

describe('WipProvider', () => {
  const mockClient = {
    defStore: {},
    templates: {},
    documents: {},
    files: {},
    registry: {},
    reporting: {},
    setAuth: vi.fn(),
  } as unknown as WipClient

  it('provides client via useWipClient', () => {
    const { result } = renderHook(() => useWipClient(), {
      wrapper: createWrapper(mockClient),
    })
    expect(result.current).toBe(mockClient)
  })

  it('throws when used outside provider', () => {
    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    expect(() => {
      renderHook(() => useWipClient(), { wrapper })
    }).toThrow('useWipClient must be used within a <WipProvider>')
  })
})
