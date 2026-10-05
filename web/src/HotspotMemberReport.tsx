import type { HotspotMemberReport as Report } from './signalReviewClient'

const percent = (value: number) => `${(value * 100).toFixed(1)}%`
const points = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}pp`

export function HotspotMemberReport({ report }: { report: Report }) {
  return <section className="hotspot-member-report" aria-label="热点成员角色">
    <header><b>领涨与辨识度</b><small>{report.effective_date} · 有效成员 {report.covered_count}/{report.member_count} · 候选 {report.candidate_count}</small></header>
    {report.status !== 'complete' ? <p>成员行情覆盖不足，暂不判定角色。</p>
      : !report.items.length ? <p>当前没有满足角色条件的成员。</p>
        : <div className="hotspot-member-table"><table>
          <thead><tr><th>标的 / 角色</th><th>相对强度</th><th>持续 / 先后</th><th>成交地位</th><th>抗跌 / 回撤</th><th>辨识度记录</th></tr></thead>
          <tbody>{report.items.map(item => <tr key={item.symbol}>
            <td><b>{item.rank}. {item.name}</b><small>{item.symbol}</small><span className={`member-role ${item.role}`}>{item.role_label}</span><small>角色内评分 {item.score.toFixed(1)}</small></td>
            <td>5日 {percent(item.return_5)}<small>超额 {points(item.excess_return_5)}</small><small>20日 {percent(item.return_20)} / 超额 {points(item.excess_return_20)}</small><small>{item.price_basis === 'forward-adjusted-as-of' ? '截至当日复权' : '原始价格口径'}</small></td>
            <td>近5日强势 {item.strength_sessions} 日<small>{item.launch_lead_sessions == null ? '启动先后未确认' : item.launch_lead_sessions > 0 ? `启动代理领先 ${item.launch_lead_sessions} 日` : item.launch_lead_sessions < 0 ? `启动代理落后 ${-item.launch_lead_sessions} 日` : '启动代理同日'}</small></td>
            <td>成交代理第 {item.amount_rank}<small>覆盖成员占比 {percent(item.amount_share)}</small><small>收盘价 × 成交量</small></td>
            <td>{item.down_market_excess == null ? '无板块下跌样本' : `下跌日超额 ${points(item.down_market_excess)}`}<small>样本 {item.down_market_sessions} 日</small><small>距10日收盘高点 {percent(item.drawdown_10)}</small></td>
            <td>{item.recognition.length ? item.recognition.map((record, index) => <small key={`${record.run_id}:${index}`}>
              {record.recognition_role.startsWith('historical') ? '历史辨识度' : '近期辨识度'} 第{record.rank}<br/>{record.effective_date}
            </small>) : '无已保存记录'}{item.recognition.length > 0 && !item.recognition_fresh && <small>记录过期，不加权</small>}</td>
          </tr>)}</tbody>
        </table></div>}
    <small>角色为观察候选；当前成员关系。成交额为代理值，排序不代表买入信号。{report.candidate_count > report.items.length ? ` 展示前 ${report.items.length} 名。` : ''}</small>
  </section>
}
