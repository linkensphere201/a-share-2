import { describe, expect, it } from 'vitest'
import { removeWindowAttachments, validateWindowAttachments, type WindowAttachment } from './windowAttachments'

const windows = [
  { id: 'list-a', type: 'instrument-list' as const, mode: 'detached' as const },
  { id: 'list-b', type: 'instrument-list' as const, mode: 'attached' as const },
  { id: 'list-c', type: 'instrument-list' as const, mode: 'detached' as const },
  { id: 'list-d', type: 'instrument-list' as const, mode: 'attached' as const },
  { id: 'chart-a', type: 'chart' as const, mode: 'attached' as const },
  { id: 'chart-b', type: 'chart' as const, mode: 'detached' as const },
]

describe('window attachments', () => {
  it('accepts multiple drivers and one source driving multiple targets', () => {
    expect(validateWindowAttachments([
      edge('edge-members', 'show-members', 'list-a', 'list-b'),
      edge('edge-a', 'show-symbol', 'list-a', 'chart-a'),
      edge('edge-b', 'show-symbol', 'list-b', 'chart-a'),
      edge('edge-c', 'show-symbol', 'list-c', 'chart-a'),
    ], windows)).toEqual([])
  })

  it('rejects incompatible member sources and fixed targets', () => {
    expect(validateWindowAttachments([
      edge('edge-a', 'show-members', 'list-b', 'list-a'),
      edge('edge-b', 'show-symbol', 'list-b', 'chart-b'),
      edge('edge-c', 'show-members', 'list-b', 'list-d'),
    ], windows)).toEqual(expect.arrayContaining([
      'show-members target is not an attached list: list-a',
      'show-members source is not a fixed list: list-b',
      'show-symbol target is not an attached chart: chart-b',
    ]))
  })

  it('removes both inbound and outbound edges with a deleted window', () => {
    const attachments = [
      edge('edge-a', 'show-members', 'list-a', 'list-b'),
      edge('edge-b', 'show-symbol', 'list-b', 'chart-a'),
    ]
    expect(removeWindowAttachments(attachments, 'list-b')).toEqual([])
  })
})

function edge(
  id: string,
  type: WindowAttachment['type'],
  sourceWindowId: string,
  targetWindowId: string,
): WindowAttachment {
  return { id, type, sourceWindowId, targetWindowId }
}
