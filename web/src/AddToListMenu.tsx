import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, ListPlus } from 'lucide-react'
import type { Instrument } from './workspace'

export type ListInstrument = Pick<Instrument, 'symbol' | 'name' | 'kind' | 'exchange'>
export type TargetInstrumentList = { id: string; title: string; instrumentCount: number }
export type AddToListMenuState = { x: number; y: number; instrument: ListInstrument }

export function AddToListMenu({ menu, targets, onAdd, onClose }: {
  menu: AddToListMenuState
  targets: TargetInstrumentList[]
  onAdd: (target: TargetInstrumentList, instrument: ListInstrument) => void
  onClose: () => void
}) {
  const [selectingTarget, setSelectingTarget] = useState(false)
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', close)
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('keydown', close)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])
  return <div className="signal-context-layer" onContextMenu={event => event.preventDefault()}
    onPointerDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="signal-context-menu" role="menu" style={{
      left: Math.max(8, Math.min(menu.x, window.innerWidth - 288)),
      top: Math.max(8, Math.min(menu.y, window.innerHeight - 260)),
      width: 'min(280px, calc(100vw - 16px))', maxHeight: 'min(250px, calc(100vh - 16px))', overflowY: 'auto',
    }}>
      <header>{menu.instrument.name}<small>{menu.instrument.symbol}</small></header>
      {!selectingTarget ? <button role="menuitem" onClick={() => setSelectingTarget(true)}>
        <ListPlus size={14}/>添加到…<ChevronRight size={13}/>
      </button> : <>
        <button role="menuitem" onClick={() => setSelectingTarget(false)}><ChevronLeft size={14}/>返回</button>
        {!targets.length && <span role="status">当前窗体组没有可写的固定列表</span>}
        {targets.map(target => <button role="menuitem" key={target.id}
          style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}
          onClick={() => onAdd(target, menu.instrument)}>
          <ListPlus size={14}/>{target.title}<small>{target.instrumentCount} 项</small>
        </button>)}
      </>}
    </div>
  </div>
}
