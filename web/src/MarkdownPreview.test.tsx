// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarkdownPreview } from './MarkdownPreview'

afterEach(cleanup)

describe('MarkdownPreview tables', () => {
  it('renders GFM-style tables with alignment and inline formatting', () => {
    render(<MarkdownPreview content={[
      '| 标的 | 状态 | 收盘价 |',
      '| :--- | :---: | ---: |',
      '| 大位科技 | **突破** | 8.14 |',
      '| 昭衍新药 | 临界\\|观察 | 21.30 |',
    ].join('\n')}/>)

    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader')).toHaveLength(3)
    expect(within(table).getByText('突破').tagName).toBe('STRONG')
    expect(within(table).getByText('临界|观察')).toBeTruthy()
    expect(within(table).getByText('8.14').className).toBe('markdown-table-right')
    expect(within(table).getByText('状态').className).toBe('markdown-table-center')
  })

  it('leaves a pipe paragraph unchanged when no separator row follows', () => {
    render(<MarkdownPreview content={'比较 A | B\n这不是表格'}/>)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText('比较 A | B 这不是表格')).toBeTruthy()
  })
})
