import { Activity, Archive, History, PanelRightClose, PanelRightOpen, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { loadAiAnalysisReports, type AiAnalysisReport } from './aiAnalysisClient'
import { AnalysisChatPanel } from './AnalysisChatPanel'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import {
  loadExactTrendAnalysis, loadTrendAnalysisRuns,
  type TrendAnalysisRun, type TrendAnalysisRunSummary,
} from './trendAnalysisClient'

export function AnalysisWorkspacePanel({
  symbol, run, followingLatest, onRunChange, onHighlightItemChange, onClose,
  selectedScenarioTarget, scenarioVisible, onScenarioTargetChange,
  onScenarioVisibleChange,
}: {
  symbol: string
  run: TrendAnalysisRun
  followingLatest: boolean
  onRunChange: (run: TrendAnalysisRun | null) => void
  onHighlightItemChange: (itemId?: string) => void
  onClose: () => void
  selectedScenarioTarget?: string
  scenarioVisible?: boolean
  onScenarioTargetChange?: (label: string) => void
  onScenarioVisibleChange?: (visible: boolean) => void
}) {
  const [runs, setRuns] = useState<TrendAnalysisRunSummary[]>([])
  const [legacyReports, setLegacyReports] = useState<AiAnalysisReport[]>([])
  const [legacyOpen, setLegacyOpen] = useState(false)
  const [selectedLegacy, setSelectedLegacy] = useState(0)
  const [chatCollapsed, setChatCollapsed] = useState(false)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      loadTrendAnalysisRuns(symbol, 'daily', controller.signal),
      loadAiAnalysisReports(symbol, 'daily', controller.signal),
    ]).then(([nextRuns, reports]) => {
      setRuns(nextRuns)
      setLegacyReports(reports)
    }).catch(() => undefined)
    return () => controller.abort()
  }, [symbol, run.run_id])

  const selectRun = async (runId: string) => {
    if (runId === '__latest__') {
      onHighlightItemChange(undefined)
      onRunChange(null)
      return
    }
    if (runId === run.run_id) return
    setLoading(true)
    try {
      onHighlightItemChange(undefined)
      onRunChange(await loadExactTrendAnalysis(runId))
    } finally {
      setLoading(false)
    }
  }
  return <aside className="analysis-workspace-panel" role="dialog" aria-label="形态分析结果与Codex对话">
    <header>
      <span><Activity size={13}/>形态分析</span>
      <div className="analysis-workspace-history">
        <History size={12}/>
        <select aria-label="分析结果轮次" value={followingLatest ? '__latest__' : run.run_id} disabled={loading} onChange={event => void selectRun(event.target.value)}>
          <option value="__latest__">跟随最新 · {runs[0]?.as_of_date ?? run.as_of_date}</option>
          {!runs.some(item => item.run_id === run.run_id) && <option value={run.run_id}>{run.as_of_date} · 当前</option>}
          {runs.map(item => <option key={item.run_id} value={item.run_id}>
            {item.as_of_date} · {item.preview ? '盘中' : '正式'} · {item.item_count}项
          </option>)}
        </select>
        <button className={legacyOpen ? 'active' : ''} title="旧AI报告" aria-label="旧AI报告" onClick={() => setLegacyOpen(value => !value)}><Archive size={12}/></button>
      </div>
      <button title="关闭形态分析" aria-label="关闭形态分析" onClick={() => {
        onHighlightItemChange(undefined)
        onClose()
      }}><X size={13}/></button>
    </header>
    <div className={chatCollapsed ? 'analysis-workspace-body chat-collapsed' : 'analysis-workspace-body'}>
      {legacyOpen ? <LegacyReports reports={legacyReports} selected={selectedLegacy} onSelected={setSelectedLegacy}/>
      : <TrendExplanationPanel
        embedded
        run={run}
        onHighlightItemChange={onHighlightItemChange}
        onClose={onClose}
        selectedScenarioTarget={selectedScenarioTarget}
        scenarioVisible={scenarioVisible}
        onScenarioTargetChange={onScenarioTargetChange}
        onScenarioVisibleChange={onScenarioVisibleChange}
      />}
      {chatCollapsed ? <button className="analysis-chat-expand" title="展开Codex对话" aria-label="展开Codex对话" onClick={() => setChatCollapsed(false)}><PanelRightOpen size={13}/></button>
      : <AnalysisChatPanel symbol={symbol} run={run} onCollapse={() => setChatCollapsed(true)} onHighlightItemChange={onHighlightItemChange}/>}
    </div>
  </aside>
}

function LegacyReports({ reports, selected, onSelected }: {
  reports: AiAnalysisReport[]; selected: number; onSelected: (index: number) => void
}) {
  const report = reports[selected]
  return <section className="legacy-ai-reports" aria-label="旧AI报告浏览">
    <header><span>旧AI报告</span>{reports.length > 0 && <select aria-label="旧AI报告版本" value={selected} onChange={event => onSelected(Number(event.target.value))}>
      {reports.map((item, index) => <option value={index} key={item.report_id}>{item.as_of_date} · 第{item.revision}版</option>)}
    </select>}</header>
    {report ? <div><h3>{report.title}</h3><small>{report.as_of_date} · {report.author} · 只读历史</small><p>{report.conclusion_markdown}</p></div>
      : <p className="analysis-chat-empty">当前标的没有旧AI报告。</p>}
  </section>
}
