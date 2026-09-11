import type { DailyConclusionReferences } from './dailyConclusionProjection'
import { dailyConclusionReferenceFor } from './dailyConclusionProjection'

export function DailyConclusionSummary({
  text, references, onHighlight,
}: {
  text: string
  references: DailyConclusionReferences
  onHighlight: (itemId?: string) => void
}) {
  return <div className="signal-fixed-summary signal-interactive-summary">
    {text.split('\n').map((line, index) => {
      const kind = line.includes('目标/空间') ? 'space'
        : line.includes('确认/失效') ? 'conditions'
        : line.includes('形态') ? 'shape'
        : line.includes('结论') || line.includes('临界状态') ? 'conclusion'
        : undefined
      const itemId = kind
        ? dailyConclusionReferenceFor(kind, references) : undefined
      return <span
        key={`${index}:${line}`}
        className={itemId ? 'has-chart-evidence' : undefined}
        tabIndex={itemId ? 0 : undefined}
        onPointerEnter={() => itemId && onHighlight(itemId)}
        onPointerLeave={() => itemId && onHighlight(undefined)}
        onFocus={() => itemId && onHighlight(itemId)}
        onBlur={() => itemId && onHighlight(undefined)}
      >{line || '\u00a0'}</span>
    })}
  </div>
}
