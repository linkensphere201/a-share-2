import type { ReactNode } from 'react'

type MarkdownPreviewProps = {
  content: string
  className?: string
  renderText?: (text: string, keyPrefix: string) => ReactNode
}

export function MarkdownPreview({ content, className, renderText }: MarkdownPreviewProps) {
  if (!content.trim()) return <div className="daily-note-empty">暂无内容</div>

  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }

    if (line.startsWith('```')) {
      const language = line.slice(3).trim()
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++])
      if (index < lines.length) index += 1
      blocks.push(<pre key={blocks.length} data-language={language || undefined}><code>{code.join('\n')}</code></pre>)
      continue
    }

    const table = parseTable(lines, index)
    if (table) {
      blocks.push(<div className="markdown-table-scroll" key={blocks.length}>
        <table>
          <thead><tr>{table.headers.map((header, cellIndex) =>
            <th className={alignmentClass(table.alignments[cellIndex])} key={cellIndex}>
              {renderInline(header, `table-${blocks.length}-head-${cellIndex}`, renderText)}
            </th>)}</tr></thead>
          <tbody>{table.rows.map((row, rowIndex) =>
            <tr key={rowIndex}>{row.map((cell, cellIndex) =>
              <td className={alignmentClass(table.alignments[cellIndex])} key={cellIndex}>
                {renderInline(cell, `table-${blocks.length}-${rowIndex}-${cellIndex}`, renderText)}
              </td>)}</tr>)}</tbody>
        </table>
      </div>)
      index = table.nextIndex
      continue
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const children = renderInline(heading[2], `heading-${blocks.length}`, renderText)
      blocks.push(level === 1 ? <h1 key={blocks.length}>{children}</h1>
        : level === 2 ? <h2 key={blocks.length}>{children}</h2>
          : <h3 key={blocks.length}>{children}</h3>)
      index += 1
      continue
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(<li key={items.length}>{renderInline(lines[index].replace(/^\s*[-*+]\s+/, ''), `ul-${blocks.length}-${items.length}`, renderText)}</li>)
        index += 1
      }
      blocks.push(<ul key={blocks.length}>{items}</ul>)
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(<li key={items.length}>{renderInline(lines[index].replace(/^\s*\d+\.\s+/, ''), `ol-${blocks.length}-${items.length}`, renderText)}</li>)
        index += 1
      }
      blocks.push(<ol key={blocks.length}>{items}</ol>)
      continue
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ''))
      blocks.push(<blockquote key={blocks.length}>{renderInline(quote.join(' '), `quote-${blocks.length}`, renderText)}</blockquote>)
      continue
    }

    if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      blocks.push(<hr key={blocks.length}/>)
      index += 1
      continue
    }

    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push(<p key={blocks.length}>{renderInline(paragraph.join(' '), `paragraph-${blocks.length}`, renderText)}</p>)
  }

  return <div className={className ? `markdown-preview ${className}` : 'markdown-preview'}>{blocks}</div>
}

type TableAlignment = 'left' | 'center' | 'right'

type ParsedTable = {
  headers: string[]
  alignments: TableAlignment[]
  rows: string[][]
  nextIndex: number
}

function parseTable(lines: string[], index: number): ParsedTable | null {
  if (index + 1 >= lines.length || !lines[index].includes('|')) return null
  const headers = splitTableRow(lines[index])
  const separators = splitTableRow(lines[index + 1])
  if (headers.length === 0 || separators.length !== headers.length) return null
  if (!separators.every(cell => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, '')))) return null
  const alignments = separators.map<TableAlignment>(cell => {
    const value = cell.replace(/\s+/g, '')
    if (value.startsWith(':') && value.endsWith(':')) return 'center'
    if (value.endsWith(':')) return 'right'
    return 'left'
  })
  const rows: string[][] = []
  let nextIndex = index + 2
  while (nextIndex < lines.length && lines[nextIndex].trim() && lines[nextIndex].includes('|')) {
    const cells = splitTableRow(lines[nextIndex]).slice(0, headers.length)
    while (cells.length < headers.length) cells.push('')
    rows.push(cells)
    nextIndex += 1
  }
  return { headers, alignments, rows, nextIndex }
}

function splitTableRow(line: string): string[] {
  let value = line.trim()
  if (value.startsWith('|')) value = value.slice(1)
  if (value.endsWith('|') && !value.endsWith('\\|')) value = value.slice(0, -1)
  const cells: string[] = []
  let current = ''
  let escaped = false
  for (const character of value) {
    if (escaped) {
      current += character
      escaped = false
    } else if (character === '\\') {
      escaped = true
    } else if (character === '|') {
      cells.push(current.trim())
      current = ''
    } else {
      current += character
    }
  }
  if (escaped) current += '\\'
  cells.push(current.trim())
  return cells
}

function alignmentClass(alignment: TableAlignment): string {
  return `markdown-table-${alignment}`
}

function isBlockStart(line: string): boolean {
  return line.startsWith('```')
    || /^(#{1,6})\s+/.test(line)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^>\s?/.test(line)
    || /^\s*([-*_])\1\1+\s*$/.test(line)
}

function renderInline(
  text: string,
  keyPrefix: string,
  renderText?: (text: string, keyPrefix: string) => ReactNode,
): ReactNode[] {
  const pattern = /(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g
  const nodes: ReactNode[] = []
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text))) {
    if (match.index > cursor) {
      const plain = text.slice(cursor, match.index)
      nodes.push(renderText?.(plain, `${keyPrefix}-plain-${nodes.length}`) ?? plain)
    }
    const token = match[0]
    const key = `${keyPrefix}-${nodes.length}`
    if (token.startsWith('`')) nodes.push(<code key={key}>{token.slice(1, -1)}</code>)
    else if (token.startsWith('**') || token.startsWith('__')) {
      const value = token.slice(2, -2)
      nodes.push(<strong key={key}>{renderText?.(value, `${key}-strong`) ?? value}</strong>)
    } else if (token.startsWith('*') || token.startsWith('_')) {
      const value = token.slice(1, -1)
      nodes.push(<em key={key}>{renderText?.(value, `${key}-em`) ?? value}</em>)
    }
    else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      const href = link ? safeHref(link[2]) : undefined
      nodes.push(href
        ? <a key={key} href={href} target="_blank" rel="noreferrer">{link![1]}</a>
        : token)
    }
    cursor = match.index + token.length
  }
  if (cursor < text.length) {
    const plain = text.slice(cursor)
    nodes.push(renderText?.(plain, `${keyPrefix}-plain-${nodes.length}`) ?? plain)
  }
  return nodes
}

function safeHref(value: string): string | undefined {
  try {
    const url = new URL(value)
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? value : undefined
  } catch {
    return undefined
  }
}
