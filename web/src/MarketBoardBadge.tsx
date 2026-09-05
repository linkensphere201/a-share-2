import type { Instrument } from './workspace'

export type MarketBoard = 'bse' | 'chinext' | 'star'

const boardPresentation: Record<MarketBoard, { glyph: string; label: string }> = {
  bse: { glyph: '北', label: '北交所' },
  chinext: { glyph: '创', label: '创业板' },
  star: { glyph: '科', label: '科创板' },
}

export function marketBoardOf(instrument: Pick<Instrument, 'symbol' | 'kind' | 'exchange'>): MarketBoard | undefined {
  if (instrument.kind !== 'stock') return undefined
  const symbol = instrument.symbol.toUpperCase()
  const exchange = instrument.exchange.toUpperCase()
  const code = symbol.split('.')[0]
  if (exchange === 'BJ' || exchange === 'BSE' || symbol.endsWith('.BJ')) return 'bse'
  if ((exchange === 'SZ' || exchange === 'SZSE' || symbol.endsWith('.SZ')) && /^(300|301)/.test(code)) return 'chinext'
  if ((exchange === 'SH' || exchange === 'SSE' || symbol.endsWith('.SH')) && /^(688|689)/.test(code)) return 'star'
  return undefined
}

export function MarketBoardBadge({ instrument }: { instrument: Pick<Instrument, 'symbol' | 'kind' | 'exchange'> }) {
  const board = marketBoardOf(instrument)
  if (!board) return null
  const presentation = boardPresentation[board]
  return <span
    className={`market-board-badge market-board-${board}`}
    title={presentation.label}
    aria-label={presentation.label}
  >{presentation.glyph}</span>
}
