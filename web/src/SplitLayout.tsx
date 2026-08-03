import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { clampSplitRatio, type WindowLayoutNode, type WindowLayoutSplit } from './layoutTree'

type SplitLayoutProps = {
  layout: WindowLayoutNode
  renderWindow: (windowId: string) => ReactNode
  onRatioCommit: (splitId: string, ratio: number) => void
}

export function SplitLayout({ layout, renderWindow, onRatioCommit }: SplitLayoutProps) {
  if (layout.type === 'window') {
    return <div className="split-pane" data-layout-node={layout.id}>{renderWindow(layout.windowId)}</div>
  }
  return <SplitBranch node={layout} renderWindow={renderWindow} onRatioCommit={onRatioCommit}/>
}

function SplitBranch({ node, renderWindow, onRatioCommit }: {
  node: WindowLayoutSplit
  renderWindow: (windowId: string) => ReactNode
  onRatioCommit: (splitId: string, ratio: number) => void
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef(false)
  const [ratio, setRatio] = useState(node.ratio)

  useEffect(() => setRatio(node.ratio), [node.ratio])

  const style = (node.direction === 'horizontal'
    ? { gridTemplateColumns: `${ratio}fr 5px ${1 - ratio}fr` }
    : { gridTemplateRows: `${ratio}fr 5px ${1 - ratio}fr` }) as CSSProperties

  const updateFromPointer = (clientX: number, clientY: number) => {
    const rect = hostRef.current?.getBoundingClientRect()
    if (!rect) return ratio
    const next = node.direction === 'horizontal'
      ? (clientX - rect.left) / rect.width
      : (clientY - rect.top) / rect.height
    const bounded = clampSplitRatio(next)
    setRatio(bounded)
    return bounded
  }

  return (
    <div
      ref={hostRef}
      className={`split-node split-${node.direction}`}
      data-layout-node={node.id}
      style={style}
    >
      <SplitLayout layout={node.first} renderWindow={renderWindow} onRatioCommit={onRatioCommit}/>
      <div
        className="split-divider"
        role="separator"
        aria-label={node.direction === 'horizontal' ? '调整左右窗口比例' : '调整上下窗口比例'}
        aria-orientation={node.direction === 'horizontal' ? 'vertical' : 'horizontal'}
        aria-valuemin={15}
        aria-valuemax={85}
        aria-valuenow={Math.round(ratio * 100)}
        onDoubleClick={() => {
          setRatio(0.5)
          onRatioCommit(node.id, 0.5)
        }}
        onPointerDown={event => {
          draggingRef.current = true
          event.currentTarget.setPointerCapture(event.pointerId)
          updateFromPointer(event.clientX, event.clientY)
        }}
        onPointerMove={event => {
          if (draggingRef.current) updateFromPointer(event.clientX, event.clientY)
        }}
        onPointerUp={event => {
          if (!draggingRef.current) return
          draggingRef.current = false
          const committed = updateFromPointer(event.clientX, event.clientY)
          event.currentTarget.releasePointerCapture(event.pointerId)
          onRatioCommit(node.id, committed)
        }}
        onPointerCancel={() => {
          draggingRef.current = false
          setRatio(node.ratio)
        }}
      />
      <SplitLayout layout={node.second} renderWindow={renderWindow} onRatioCommit={onRatioCommit}/>
    </div>
  )
}
