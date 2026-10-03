import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { FillRunDetail } from '../src/api/types'
import { getFillRun } from '../src/api/fillRuns.api'
import { useFillRunStore } from '../src/stores/fillRun.store'

vi.mock('../src/api/fillRuns.api', () => ({
  getFillRun: vi.fn(), listFillRuns: vi.fn(), createSimpleFillRun: vi.fn(), cancelFillRun: vi.fn(),
}))

beforeEach(() => { setActivePinia(createPinia()); vi.mocked(getFillRun).mockReset() })

it('never replaces a newer run with an older request that finishes late', async () => {
  const pending: Record<string, (run: FillRunDetail) => void> = {}
  vi.mocked(getFillRun).mockImplementation((id) => new Promise((resolve) => { pending[id] = resolve }))
  const store = useFillRunStore()
  const oldRequest = store.loadRun('old')
  const newRequest = store.loadRun('new')
  pending.new({ id: 'new' } as FillRunDetail)
  await newRequest
  pending.old({ id: 'old' } as FillRunDetail)
  await oldRequest
  expect(store.detail?.id).toBe('new')
  expect(store.current?.id).toBe('new')
})
