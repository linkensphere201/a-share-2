import { afterEach, describe, expect, it, vi } from 'vitest'

import { openDefaultLearningSystem } from './learningClient'


afterEach(() => vi.unstubAllGlobals())

describe('openDefaultLearningSystem', () => {
  it('opens the available default course', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [
          {
            system_id: 'other-system', title: '其他体系', methodology: 'other',
            status: 'published', default: false, available: true,
          },
          {
            system_id: 'trend-genggui', title: '趋势耿鬼', methodology: 'trend-trading',
            status: 'published', default: true, available: true,
          },
        ] }),
      })
      .mockResolvedValueOnce({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    const system = await openDefaultLearningSystem()

    expect(system.system_id).toBe('trend-genggui')
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/learning/systems/trend-genggui/open',
      { method: 'POST' },
    )
  })

  it('reports an empty published catalog', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [] }),
    }))

    await expect(openDefaultLearningSystem()).rejects.toThrow('尚未发布')
  })
})
