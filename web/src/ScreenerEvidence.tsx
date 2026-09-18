import type { ScreenerCandidate } from './screenerClient'
import { candidateStageLabel, pullbackStates, signed, latestClose } from './screenerPresentation'
import { accumulationStageLabel, accumulationPatternLabel, accumulationStyleLabel, firstPullbackVolumeLabel } from './trendExplanation'

export function ScreenerEvidence({ selected }: { selected?: ScreenerCandidate }) {
  if (!selected || selected.evidence_complete === false) return null
  return <>
        {selected.state === 'accumulating' && <footer className="screener-evidence">
          <span><small>形态阶段</small>{accumulationStageLabel(selected.evidence.stage)}</span>
          {selected.evidence.compact_platform && <>
            <span><small>平台分类 / 紧凑度</small>{accumulationStyleLabel(selected.evidence.platform_style)} / {selected.evidence.compact_platform.score.toFixed(1)}</span>
            <span><small>近10日区间</small>{selected.evidence.compact_platform.last10?.range_percent.toFixed(2) ?? '--'}%</span>
            <span><small>小实体占比</small>{((selected.evidence.compact_platform.last10?.small_body_fraction ?? 0) * 100).toFixed(0)}%</span>
            <span><small>辨识度排序加分</small>+{selected.evidence.recognition_rank_bonus ?? 0}</span>
          </>}
          <span><small>结构类型</small>{accumulationPatternLabel(selected.evidence.pattern_type)}{selected.evidence.gentle_retest ? ' · 温和回踩' : ''}</span>
          {selected.evidence.pattern_type === 'secondary-base' && <span><small>回踩 / 反弹均量</small>{selected.evidence.pullback_volume_ratio?.toFixed(2) ?? '--'}x</span>}
          <span><small>前期阴跌</small>{signed(selected.evidence.decline_return_percent ?? 0)}%</span>
          <span><small>均线发散</small>{selected.evidence.ma_divergence_percent?.toFixed(2)}%</span>
          <span><small>底部抬升</small>{signed(selected.evidence.bottom_lift_percent ?? 0)}%</span>
          <span><small>平台振幅</small>{selected.evidence.platform_range_percent?.toFixed(2)}%</span>
          <span><small>平台涨跌</small>{signed(selected.evidence.platform_return_percent ?? 0)}%</span>
          <span><small>小实体 K 线</small>{selected.evidence.small_body_sessions ?? 0}/{selected.evidence.platform_sessions ?? 10}</span>
          <span><small>下跌减速</small>{selected.evidence.decline_slowing === undefined ? '--' : selected.evidence.decline_slowing ? '已确认' : '待确认'}</span>
          <span><small>{selected.evidence.demand_regime === 'dry-up-retest' ? '缩量承接' : '温和放量'}</small>{selected.evidence.platform_volume_ratio?.toFixed(2)}x</span>
          <span><small>红绿均量比</small>{selected.evidence.average_up_down_volume_ratio?.toFixed(2) ?? '--'}</span>
          <span><small>去最大量日</small>{selected.evidence.robust_up_down_volume_ratio?.toFixed(2) ?? '--'}</span>
          <span><small>期间涨停</small>{selected.evidence.limit_up_count ?? '--'} 次（允许）</span>
          <span><small>结构失效位</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '--'}</span>
          {selected.evidence.score_components && <span><small>下跌 / 抬升 / 平台 / 承接</small>{Object.values(selected.evidence.score_components).map(value => value.toFixed(1)).join(' / ')}</span>}
          {selected.evidence.missing_evidence?.includes('turnover-unavailable') && <span><small>辅助证据</small>换手率暂缺</span>}
        </footer>}
        {selected?.evidence.kind === 'long-platform-range' && <footer className="screener-evidence">
          <span><small>平台类型</small>{selected.evidence.platform_type === 'low-base' ? '低位筑底' : selected.evidence.platform_type === 'continuation' ? '上升中继' : '横盘整理'}</span>
          <span><small>形态阶段</small>{candidateStageLabel(selected)}</span>
          <span><small>平台区间</small>{selected.evidence.start_date} ~ {selected.evidence.end_date}</span>
          <span><small>整理时长</small>{selected.evidence.platform_sessions} 个交易日</span>
          <span><small>平台振幅 / 近20日振幅</small>{selected.evidence.platform_range_percent?.toFixed(2)}% / {selected.evidence.recent_range_percent?.toFixed(2)}%</span>
          <span><small>近20日 / 前段均量</small>{selected.evidence.recent_history_volume_ratio?.toFixed(2)}x</span>
          <span><small>重心漂移</small>{selected.evidence.center_drift_percent?.toFixed(2)}%</span>
          <span><small>下沿抬升</small>{selected.evidence.floor_lift_percent?.toFixed(2)}%</span>
          <span><small>区间位置</small>{((selected.evidence.range_position ?? 0) * 100).toFixed(0)}%</span>
          <span><small>MA60 / MA240 变化</small>{selected.evidence.ma60_slope_percent?.toFixed(2) ?? '-'}% / {selected.evidence.ma240_slope_percent?.toFixed(2) ?? '-'}%</span>
        </footer>}
        {selected?.evidence.kind === 'deep-drawdown-range' && <footer className="screener-evidence">
          <span><small>形态</small>深跌缩量整理</span>
          <span><small>匹配区间</small>{selected.evidence.window_start_date} ~ {selected.evidence.as_of_date}</span>
          <span><small>参照区间</small>{selected.evidence.reference?.symbol} · {selected.evidence.reference?.start_date} ~ {selected.evidence.reference?.end_date}</span>
          <span><small>60日涨跌</small>{signed(selected.evidence.return_60d_percent ?? 0)}%</span>
          <span><small>最大收盘回撤</small>{selected.evidence.max_drawdown_percent?.toFixed(2)}%</span>
          <span><small>近20日收盘振幅</small>{selected.evidence.close_range_20d_percent?.toFixed(2)}%</span>
          <span><small>近10日 / 前20日均量</small>{((selected.evidence.recent_early_volume_ratio ?? 0) * 100).toFixed(1)}%</span>
          <span><small>价格 / 末段相关性</small>{selected.evidence.price_correlation?.toFixed(3)} / {selected.evidence.recent_correlation?.toFixed(3)}</span>
          <span><small>价格 / 末段 / 幅度 / 量能得分</small>{(['price_path', 'recent_path', 'amplitude', 'volume_path'] as const).map(key => selected.evidence.similarity_components?.[key]?.toFixed(1) ?? '-').join(' / ')}</span>
          <span><small>价格口径</small>{selected.evidence.price_basis === 'forward-adjusted-as-of' ? '截至当日前复权' : '原始价格'}</span>
        </footer>}
        {selected?.evidence.kind === 'bull-flag-range' && <footer className="screener-evidence">
          <span><small>形态阶段</small>旗面盘整中</span>
          <span><small>启动距今</small>{selected.evidence.launch_age_sessions} 个交易日</span>
          <span><small>旗杆涨幅</small>{selected.evidence.impulse_gain_percent?.toFixed(2)}%</span>
          <span><small>旗面区间</small>{selected.evidence.start_date} ~ {selected.evidence.end_date}</span>
          <span><small>盘整天数</small>{selected.evidence.flag_sessions} 日</span>
          <span><small>重心变化</small>{selected.evidence.center_drift_percent?.toFixed(2)}%</span>
          <span><small>旗面振幅</small>{selected.evidence.flag_range_percent?.toFixed(2)}%</span>
          <span><small>盘整 / 上涨均量</small>{selected.evidence.flag_pole_volume_ratio?.toFixed(2)}x</span>
          <span><small>后段 / 前段均量</small>{selected.evidence.late_early_volume_ratio?.toFixed(2)}x</span>
          <span><small>旗杆最低价</small>{selected.evidence.pole_low?.toFixed(2)}</span>
        </footer>}
        {selected && selected.evidence.kind !== 'bull-flag-range' && pullbackStates.includes(selected.state) && <footer className="screener-evidence">
          <span><small>形态阶段</small>{candidateStageLabel(selected)}</span>
          {selected.evidence.flag_window && <>
            <span><small>滚动观察窗口</small>{selected.evidence.observation_window_sessions} 个交易日</span>
            <span><small>整理区间</small>{selected.evidence.flag_window.start_date} ~ {selected.evidence.flag_window.end_date}</span>
            <span><small>整理阶段</small>{selected.evidence.launch_type === 'low-base-platform' ? '低位平台' : selected.evidence.flag_window.phase === 'early' ? '早期旗形观察' : '旗形整理观察'} · {selected.evidence.flag_window.sessions} 日</span>
          </>}
          <span><small>启动日期</small>{selected.evidence.launch_date ?? '--'}</span>
          <span><small>确认日期</small>{selected.evidence.confirmation_date ?? '尚未确认'}</span>
          <span><small>启动涨幅</small>{selected.evidence.impulse_gain_percent?.toFixed(2) ?? '--'}%</span>
          <span><small>回踩幅度</small>{selected.evidence.pullback_depth_percent?.toFixed(2) ?? '--'}%</span>
          <span><small>回踩 / 启动均量</small>{selected.evidence.pullback_volume_ratio?.toFixed(2) ?? '--'}x</span>
          {selected.evidence.launch_type === 'low-base-platform' && <>
            {selected.evidence.platform_shape && <>
              <span><small>独立平台</small>{selected.evidence.platform_shape.start_date} ~ {selected.evidence.platform_shape.end_date} · {selected.evidence.platform_shape.sessions} 日</span>
              <span><small>平台价格漂移</small>{selected.evidence.platform_shape.close_drift_percent.toFixed(2)}%</span>
              <span><small>小实体占比</small>{(selected.evidence.platform_shape.small_body_fraction * 100).toFixed(0)}%</span>
            </>}
            <span><small>启动前距60日低点</small>{selected.evidence.origin_above_context_low_percent?.toFixed(2) ?? '--'}%</span>
            <span><small>平台振幅</small>{selected.evidence.platform_range_percent?.toFixed(2) ?? '--'}%</span>
            <span><small>回踩 / 平台均量</small>{selected.evidence.pullback_platform_volume_ratio?.toFixed(2) ?? '--'}x</span>
          </>}
          {selected.evidence.launch_type === 'strong-momentum' && <>
            <span><small>启动类型</small>连续强阳启动</span>
            <span><small>量能类型</small>{firstPullbackVolumeLabel(selected.evidence.volume_regime)}</span>
            <span><small>回踩均量 / 启动至峰值最大日量</small>{selected.evidence.pullback_turnover_ratio?.toFixed(2) ?? '--'}x</span>
          </>}
          <span><small>失效位（收盘口径）</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '--'}</span>
          {selected.evidence.launch_type !== 'low-base-platform' && <>
            <span><small>前高参考</small>{selected.evidence.first_target_price?.toFixed(2) ?? '--'}</span>
            <span><small>参考盈亏比</small>{selected.evidence.first_risk_reward?.toFixed(2) ?? '--'}</span>
          </>}
          <span><small>证据边界</small>仅日线量价；板块共振、分时承接未验证</span>
        </footer>}
        {selected && selected.state !== 'shape-match' && selected.state !== 'accumulating' && !pullbackStates.includes(selected.state) && <footer className="screener-evidence">
          <span><small>边界</small>{selected.evidence.projected_price?.toFixed(2)}</span>
          <span><small>收盘</small>{latestClose(selected).toFixed(2)}</span>
          <span><small>距斜边</small>{signed(selected.evidence.distance_percent ?? 0)}%</span>
          <span><small>失效位</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '—'}</span>
          <span><small>目标位</small>{selected.evidence.first_target_price?.toFixed(2) ?? '—'}</span>
          <span><small>盈亏比</small>{selected.evidence.first_risk_reward?.toFixed(2) ?? '—'}</span>
          <span><small>小周期 14</small>{signed(selected.evidence.small_14?.return_percent ?? 0)}%</span>
          <span><small>中周期 28</small>{signed(selected.evidence.medium_28?.return_percent ?? 0)}%</span>
        </footer>}
</>
}
