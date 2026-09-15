// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AddToListMenu } from './AddToListMenu'

afterEach(cleanup)

it('shows empty targets and closes on outside click', () => {
  const close = vi.fn()
  const add = vi.fn()
  render(<AddToListMenu menu={{ x: 9999, y: 9999,
    instrument: { symbol: 'board', name: 'Board', kind: 'board', exchange: 'SW' } }}
    targets={[]} onAdd={add} onClose={close}/>)
  fireEvent.click(screen.getByRole('menuitem', { name: '添加到…' }))
  expect(screen.getByRole('status').textContent).toContain('没有可写')
  expect(parseInt(screen.getByRole('menu').style.left)).toBeLessThan(window.innerWidth)
  fireEvent.pointerDown(screen.getByRole('menu').parentElement!)
  expect(close).toHaveBeenCalledOnce()
  expect(add).not.toHaveBeenCalled()
})
