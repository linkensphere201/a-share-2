import type { ReactNode } from 'react'

export function MarkdownPreview({ content }: { content: string }) {
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

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const children = renderInline(heading[2], `heading-${blocks.length}`)
      blocks.push(level === 1 ? <h1 key={blocks.length}>{children}</h1>
        : level === 2 ? <h2 key={blocks.length}>{children}</h2>
          : <h3 key={blocks.length}>{children}</h3>)
      index += 1
      continue
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(<li key={items.length}>{renderInline(lines[index].replace(/^\s*[-*+]\s+/, ''), `ul-${blocks.length}-${items.length}`)}</li>)
        index += 1
      }
      blocks.push(<ul key={blocks.length}>{items}</ul>)
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(<li key={items.length}>{renderInline(lines[index].replace(/^\s*\d+\.\s+/, ''), `ol-${blocks.length}-${items.length}`)}</li>)
        index += 1
      }
      blocks.push(<ol key={blocks.length}>{items}</ol>)
      continue
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ''))
      blocks.push(<blockquote key={blocks.length}>{renderInline(quote.join(' '), `quote-${blocks.length}`)}</blockquote>)
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
    blocks.push(<p key={blocks.length}>{renderInline(paragraph.join(' '), `paragraph-${blocks.length}`)}</p>)
  }

  return <div className="markdown-preview">{blocks}</div>
}

function isBlockStart(line: string): boolean {
  return line.startsWith('```')
    || /^(#{1,6})\s+/.test(line)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^>\s?/.test(line)
    || /^\s*([-*_])\1\1+\s*$/.test(line)
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g
  const nodes: ReactNode[] = []
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text))) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index))
    const token = match[0]
    const key = `${keyPrefix}-${nodes.length}`
    if (token.startsWith('`')) nodes.push(<code key={key}>{token.slice(1, -1)}</code>)
    else if (token.startsWith('**') || token.startsWith('__')) nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    else if (token.startsWith('*') || token.startsWith('_')) nodes.push(<em key={key}>{token.slice(1, -1)}</em>)
    else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      const href = link ? safeHref(link[2]) : undefined
      nodes.push(href
        ? <a key={key} href={href} target="_blank" rel="noreferrer">{link![1]}</a>
        : token)
    }
    cursor = match.index + token.length
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
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
