import { Activity, X } from 'lucide-react'
import { AnalysisChatPanel } from './AnalysisChatPanel'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export function AnalysisWorkspacePanel({
  symbol, run, onHighlightItemChange, onClose,
}: {
  symbol: string
  run: TrendAnalysisRun
  onHighlightItemChange: (itemId?: string) => void
  onClose: () => void
}) {
  return <aside className="analysis-workspace-panel" role="dialog" aria-label="形态分析结果与Codex对话">
    <header>
      <span><Activity size={13}/>形态分析</span>
      <button title="关闭形态分析" aria-label="关闭形态分析" onClick={() => {
        onHighlightItemChange(undefined)
        onClose()
      }}><X size={13}/></button>
    </header>
    <div className="analysis-workspace-body">
      <TrendExplanationPanel
        embedded
        run={run}
        onHighlightItemChange={onHighlightItemChange}
        onClose={onClose}
      />
      <AnalysisChatPanel symbol={symbol} run={run} onHighlightItemChange={onHighlightItemChange}/>
    </div>
  </aside>
}
