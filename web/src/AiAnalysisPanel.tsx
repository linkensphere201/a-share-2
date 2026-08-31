import { Bot, X } from 'lucide-react'
import {
  aiReferenceItemId,
  type AiAnalysisReference,
  type AiAnalysisReport,
} from './aiAnalysisClient'

export function AiAnalysisPanel({
  report,
  onHighlightItemChange,
  onClose,
}: {
  report: AiAnalysisReport | null
  onHighlightItemChange: (itemId?: string) => void
  onClose: () => void
}) {
  if (!report) return <aside className="ai-analysis-panel empty" role="dialog" aria-label="AI形态分析">
    <header><span><Bot size={13}/>AI形态分析</span><button title="关闭AI形态分析" aria-label="关闭AI形态分析" onClick={onClose}><X size={13}/></button></header>
    <div className="ai-analysis-empty">当前标的还没有AI分析结论。通过 StockHarness MCP 写入后，重新打开此面板即可查看。</div>
  </aside>
  const references = new Map(report.references.map(item => [item.code, item]))
  const highlight = (reference?: AiAnalysisReference) => (
    onHighlightItemChange(reference ? aiReferenceItemId(report, reference) : undefined)
  )
  const referenceButtons = (codes: string[]) => codes.map(code => {
    const reference = references.get(code)
    return reference && <ReferenceCode key={code} reference={reference} onHighlight={highlight}/>
  })
  return (
    <aside className="ai-analysis-panel" role="dialog" aria-label="AI形态分析">
      <header>
        <span><Bot size={13}/>AI形态分析</span>
        <button title="关闭AI形态分析" aria-label="关闭AI形态分析" onClick={() => { highlight(); onClose() }}><X size={13}/></button>
      </header>
      <div className="ai-analysis-meta">
        <span>{report.title}</span>
        <small>截至 {report.as_of_date} · 第 {report.revision} 版 · {report.author}</small>
      </div>
      <div className="ai-analysis-body">
        <section>
          <h3>关键位</h3>
          <div className="ai-reference-list">
            {report.framework.key_level_codes.map(code => {
              const reference = references.get(code)
              return reference && <article
                key={code}
                tabIndex={0}
                onPointerEnter={() => highlight(reference)}
                onPointerLeave={() => highlight()}
                onFocus={() => highlight(reference)}
                onBlur={() => highlight()}
              >
                <ReferenceCode reference={reference} onHighlight={highlight}/>
                <span>{reference.label}</span><small>{reference.detail}</small>
              </article>
            })}
          </div>
        </section>
        <section>
          <h3>形态与趋势</h3>
          {(['small', 'medium'] as const).map(horizon => {
            const view = report.framework.structures.find(item => item.horizon === horizon)
            if (!view) return null
            return <article className="ai-structure-view" key={horizon}>
              <div><span>{horizon === 'small' ? '小周期 · 7–14日' : '中周期 · 14–28日'}</span>{referenceButtons(view.reference_codes)}</div>
              <dl><dt>趋势</dt><dd>{view.trend}</dd><dt>形态</dt><dd>{view.pattern}</dd><dt>状态</dt><dd>{view.state}</dd></dl>
            </article>
          })}
        </section>
        <section>
          <h3>盈亏比</h3>
          {report.framework.risk_reward.map((scenario, index) => <article className="ai-risk-reward" key={`${scenario.name}-${index}`}>
            <div><span>{scenario.name}</span><i>{scenario.risk_reward_ratio.toFixed(2)}R</i>{referenceButtons(scenario.reference_codes)}</div>
            <p>{scenario.trigger}</p>
            <dl><dt>入场</dt><dd>{scenario.entry_price.toFixed(2)}</dd><dt>止损</dt><dd>{scenario.stop_price.toFixed(2)}</dd><dt>目标</dt><dd>{scenario.target_price.toFixed(2)}</dd></dl>
          </article>)}
        </section>
        <section>
          <h3>AI结论</h3>
          <div className="ai-analysis-conclusion">{renderConclusion(report.conclusion_markdown, references, highlight)}</div>
        </section>
      </div>
    </aside>
  )
}

function ReferenceCode({
  reference,
  onHighlight,
}: {
  reference: AiAnalysisReference
  onHighlight: (reference?: AiAnalysisReference) => void
}) {
  return <button
    className="ai-reference-code"
    title={`${reference.code} · ${reference.label}`}
    onPointerEnter={() => onHighlight(reference)}
    onPointerLeave={() => onHighlight()}
    onFocus={() => onHighlight(reference)}
    onBlur={() => onHighlight()}
  >{reference.code}</button>
}

function renderConclusion(
  text: string,
  references: Map<string, AiAnalysisReference>,
  onHighlight: (reference?: AiAnalysisReference) => void,
) {
  return text.split(/(\[[A-Z][A-Z0-9_-]*\])/g).map((part, index) => {
    const match = /^\[([A-Z][A-Z0-9_-]*)\]$/.exec(part)
    const reference = match ? references.get(match[1]) : undefined
    return reference
      ? <ReferenceCode key={`${part}-${index}`} reference={reference} onHighlight={onHighlight}/>
      : part
  })
}
