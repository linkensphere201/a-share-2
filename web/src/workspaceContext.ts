import { loadSymbolDrawings } from './drawingStore'
import { deriveReferencedSymbols, type Instrument, type WindowGroupState } from './workspace'

const workspaceContextSchemaVersion = '1.0'

export function buildWorkspaceContext(
  group: WindowGroupState,
  resolvedByWindow: Record<string, string[]> = {},
  now = new Date(),
) {
  const chartSymbols = new Set<string>()
  const chartInstruments = new Map<string, Instrument>()
  const windows = group.windows.map(window => {
    const base = {
      id: window.id,
      type: window.type,
      title: window.title,
      mode: window.mode,
      focused: window.id === group.focusedWindowId,
      maximized: window.id === group.maximizedWindowId,
    }
    if (window.type === 'chart') {
      chartSymbols.add(window.instrument.symbol)
      chartInstruments.set(window.instrument.symbol, window.instrument)
      return {
        ...base,
        instrument: instrumentReference(window.instrument),
        chart: {
          range: window.chart.range,
          coordinate_mode: window.chart.priceMode,
          visible_start: window.chart.visibleRange?.from,
          visible_end: window.chart.visibleRange?.to,
          volume_visible: window.chart.volumeVisible,
          indicator: window.chart.indicator,
        },
      }
    }
    return {
      ...base,
      instruments: window.content.instruments.map(instrumentReference),
      resolved_symbols: resolvedByWindow[window.id] ?? [],
      selected_symbol: window.selectedSymbol,
      member_source_window_id: window.memberSourceWindowId,
    }
  })
  return {
    schema_version: workspaceContextSchemaVersion,
    published_at: now.toISOString(),
    active_group_id: group.id,
    active_group_name: group.name,
    focused_window_id: group.focusedWindowId,
    maximized_window_id: group.maximizedWindowId,
    referenced_symbols: deriveReferencedSymbols(group, resolvedByWindow),
    windows,
    attachments: group.attachments.map(attachment => ({
      id: attachment.id,
      type: attachment.type,
      source_window_id: attachment.sourceWindowId,
      target_window_id: attachment.targetWindowId,
    })),
    drawings_by_symbol: Object.fromEntries([...chartSymbols].map(symbol => [
      symbol,
      loadSymbolDrawings(drawingTarget(chartInstruments.get(symbol), symbol)).map(drawing => ({
        id: drawing.id,
        kind: drawing.kind,
        symbol: drawing.symbol,
        anchors: drawing.anchors.map(anchor => ({ ...anchor })),
        coordinate_mode: drawing.coordinateMode,
        style: { ...drawing.style },
        visible: drawing.visible,
        created_at: drawing.createdAt,
        updated_at: drawing.updatedAt,
      })),
    ])),
  }
}

export async function publishWorkspaceContext(
  context: ReturnType<typeof buildWorkspaceContext>,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/workspace-context', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(context),
    signal,
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}

function instrumentReference(instrument: Instrument) {
  return {
    symbol: instrument.symbol,
    name: instrument.name,
    kind: instrument.kind,
    exchange: instrument.exchange,
    product_code: instrument.product_code,
    lifecycle_status: instrument.lifecycle_status,
    contract_month: instrument.contract_month,
    series_kind: instrument.series_kind,
    series_variant: instrument.series_variant,
    price_basis: instrument.price_basis,
    rule_version: instrument.rule_version,
  }
}

function drawingTarget(instrument: Instrument | undefined, symbol: string) {
  return instrument ? {
    symbol,
    instrumentKind: instrument.kind,
    priceBasis: instrument.price_basis,
    ruleVersion: instrument.rule_version,
  } : symbol
}
