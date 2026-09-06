import type { Instrument } from './workspace'

export type InstrumentBoardTag = {
  board_symbol: string
  name: string
  classification: 'industry' | 'concept'
  position: number
  source_system: string
  selection_score: number
  selection_reason: string
  algorithm_version: string
  generated_at_ms: number
}

type BoardTagRecord = { symbol: string; tags: InstrumentBoardTag[] }

export async function fetchInstrumentBoardTags(
  symbols: string[], signal?: AbortSignal,
): Promise<Record<string, InstrumentBoardTag[]>> {
  const records: BoardTagRecord[] = []
  for (let offset = 0; offset < symbols.length; offset += 500) {
    const params = new URLSearchParams()
    symbols.slice(offset, offset + 500).forEach(symbol => params.append('symbol', symbol))
    const response = await fetch(`/api/instrument-board-tags?${params}`, { signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as { items: BoardTagRecord[] }
    records.push(...body.items)
  }
  return Object.fromEntries(records.map(item => [item.symbol, item.tags]))
}

export function isBoardTaggable(instrument: Pick<Instrument, 'kind'>): boolean {
  return instrument.kind === 'stock'
}
