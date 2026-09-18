// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { LayoutManager } from './LayoutManager'
import { createDefaultWorkspace, type WorkspaceState } from './workspace'
import { collectLayoutWindowIds, validateLayoutTree } from './layoutTree'

afterEach(cleanup)

function setup() {
  let latest = createDefaultWorkspace()
  const original = structuredClone(latest)
  function Host() {
    const [workspace, setWorkspace] = useState(latest)
    latest = workspace
    return <LayoutManager workspace={workspace} onChange={setWorkspace} onClose={() => {}}/>
  }
  const view = render(<Host />)
  const tiles = () => [...view.container.querySelectorAll<HTMLButtonElement>('.layout-preview-window')]
  return { tiles, original, current: () => latest }
}

function drag(source: HTMLElement, target: HTMLElement, x = 50, y = 50) {
  const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' }
  fireEvent.dragStart(source, { dataTransfer })
  vi.spyOn(target, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 100, height: 100 } as DOMRect)
  for (const type of ['dragover', 'drop']) {
    const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y })
    Object.defineProperty(event, 'dataTransfer', { value: dataTransfer })
    fireEvent(target, event)
  }
  fireEvent.dragEnd(source, { dataTransfer })
}

function expectIdentity(current: WorkspaceState, original: WorkspaceState) {
  expect(current.groups[0].windows).toEqual(original.groups[0].windows)
  expect(current.groups[0].attachments).toEqual(original.groups[0].attachments)
  expect(validateLayoutTree(current.groups[0].layout, original.groups[0].windows.map(w => w.id))).toEqual([])
}

it('swaps dragged windows and records one undoable, redoable layout edit', () => {
  const { tiles, original, current } = setup()
  drag(tiles()[0], tiles()[1])
  expect(collectLayoutWindowIds(current().groups[0].layout)).toEqual(collectLayoutWindowIds(original.groups[0].layout).reverse())
  expectIdentity(current(), original)
  fireEvent.click(screen.getByRole('button', { name: '撤销' }))
  expect(current().groups[0].layout).toEqual(original.groups[0].layout)
  expect((screen.getByRole('button', { name: '撤销' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '重做' }))
  expect(current().groups[0].layout).not.toEqual(original.groups[0].layout)
})

it('moves a window to the lower edge without changing attachments', () => {
  const { tiles, original, current } = setup()
  drag(tiles()[0], tiles()[1], 50, 95)
  const layout = current().groups[0].layout
  expect(layout.type === 'split' && layout.direction).toBe('vertical')
  expectIdentity(current(), original)
})

it('ignores self-drops and external drops without adding history', () => {
  const { tiles, original, current } = setup()
  fireEvent.drop(tiles()[0])
  drag(tiles()[0], tiles()[0])
  expect(current()).toEqual(original)
  expect((screen.getByRole('button', { name: '撤销' }) as HTMLButtonElement).disabled).toBe(true)
})
