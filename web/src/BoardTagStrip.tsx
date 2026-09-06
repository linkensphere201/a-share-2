import { useEffect, useState } from 'react'
import { fetchInstrumentBoardTags, isBoardTaggable, type InstrumentBoardTag } from './boardTags'
import type { Instrument } from './workspace'

type BoardTagStripProps = {
  instrument: Pick<Instrument, 'symbol' | 'kind'>
  tags?: InstrumentBoardTag[]
  compact?: boolean
}

export function BoardTagStrip({ instrument, tags, compact = false }: BoardTagStripProps) {
  const [loaded, setLoaded] = useState<InstrumentBoardTag[]>([])
  const shouldLoad = tags === undefined && isBoardTaggable(instrument)
  useEffect(() => {
    if (!shouldLoad) return
    const controller = new AbortController()
    fetchInstrumentBoardTags([instrument.symbol], controller.signal)
      .then(result => setLoaded(result[instrument.symbol] ?? []))
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setLoaded([])
      })
    return () => controller.abort()
  }, [instrument.symbol, shouldLoad])

  const visible = tags ?? loaded
  if (!isBoardTaggable(instrument) || visible.length === 0) return null
  return <span className={compact ? 'board-tag-strip compact' : 'board-tag-strip'}>
    {visible.map(tag => <i
      key={`${tag.classification}:${tag.board_symbol}`}
      className={tag.classification}
      title={`${tag.classification === 'industry' ? '行业板块' : '概念板块'} · ${tag.name} · ${tag.source_system}`}
    >{tag.classification === 'industry' ? '行业' : '概念'}·{tag.name}</i>)}
  </span>
}
