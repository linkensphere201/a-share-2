export type SplitDirection = 'horizontal' | 'vertical'

export type WindowLayoutLeaf = {
  type: 'window'
  id: string
  windowId: string
}

export type WindowLayoutSplit = {
  type: 'split'
  id: string
  direction: SplitDirection
  ratio: number
  first: WindowLayoutNode
  second: WindowLayoutNode
}

export type WindowLayoutNode = WindowLayoutLeaf | WindowLayoutSplit

export const minimumSplitRatio = 0.15
export const maximumSplitRatio = 0.85

export function createLayoutTree(windowIds: string[], prefix = 'layout'): WindowLayoutNode {
  if (windowIds.length === 0) throw new Error('A layout requires at least one window')
  if (windowIds.length === 3) {
    return splitNode(`${prefix}-root`, 'horizontal', 0.5,
      leafNode(`${prefix}-0`, windowIds[0]),
      splitNode(`${prefix}-right`, 'vertical', 0.5,
        leafNode(`${prefix}-1`, windowIds[1]),
        leafNode(`${prefix}-2`, windowIds[2]),
      ),
    )
  }
  return buildBalanced(windowIds, prefix, 'horizontal')
}

export function collectLayoutWindowIds(node: WindowLayoutNode): string[] {
  return node.type === 'window'
    ? [node.windowId]
    : [...collectLayoutWindowIds(node.first), ...collectLayoutWindowIds(node.second)]
}

export function validateLayoutTree(node: WindowLayoutNode, windowIds: Iterable<string>): string[] {
  const expected = new Set(windowIds)
  const nodeIds = new Set<string>()
  const seenWindows = new Set<string>()
  const issues: string[] = []

  visit(node, current => {
    if (nodeIds.has(current.id)) issues.push(`duplicate node id: ${current.id}`)
    nodeIds.add(current.id)
    if (current.type === 'split') {
      if (current.ratio < minimumSplitRatio || current.ratio > maximumSplitRatio) {
        issues.push(`invalid split ratio: ${current.id}`)
      }
      return
    }
    if (!expected.has(current.windowId)) issues.push(`unknown window: ${current.windowId}`)
    if (seenWindows.has(current.windowId)) issues.push(`duplicate window: ${current.windowId}`)
    seenWindows.add(current.windowId)
  })

  for (const windowId of expected) {
    if (!seenWindows.has(windowId)) issues.push(`missing window: ${windowId}`)
  }
  return issues
}

export function parseLayoutTree(value: unknown): WindowLayoutNode | undefined {
  if (!isRecord(value) || typeof value.id !== 'string') return undefined
  if (value.type === 'window' && typeof value.windowId === 'string') {
    return leafNode(value.id, value.windowId)
  }
  if (value.type !== 'split' || (value.direction !== 'horizontal' && value.direction !== 'vertical')) return undefined
  if (typeof value.ratio !== 'number' || !Number.isFinite(value.ratio)) return undefined
  const first = parseLayoutTree(value.first)
  const second = parseLayoutTree(value.second)
  if (!first || !second) return undefined
  return splitNode(value.id, value.direction, clampSplitRatio(value.ratio), first, second)
}

export function splitLayoutWindow(
  node: WindowLayoutNode,
  targetWindowId: string,
  newWindowId: string,
  direction: SplitDirection,
  splitId: string,
  leafId: string,
  placement: 'before' | 'after' = 'after',
): WindowLayoutNode {
  if (node.type === 'window') {
    if (node.windowId !== targetWindowId) return node
    const added = leafNode(leafId, newWindowId)
    return placement === 'before'
      ? splitNode(splitId, direction, 0.5, added, node)
      : splitNode(splitId, direction, 0.5, node, added)
  }
  return {
    ...node,
    first: splitLayoutWindow(node.first, targetWindowId, newWindowId, direction, splitId, leafId, placement),
    second: splitLayoutWindow(node.second, targetWindowId, newWindowId, direction, splitId, leafId, placement),
  }
}

export function removeLayoutWindow(node: WindowLayoutNode, windowId: string): WindowLayoutNode | undefined {
  if (node.type === 'window') return node.windowId === windowId ? undefined : node
  const first = removeLayoutWindow(node.first, windowId)
  const second = removeLayoutWindow(node.second, windowId)
  if (!first) return second
  if (!second) return first
  return { ...node, first, second }
}

export function updateSplitRatio(node: WindowLayoutNode, splitId: string, ratio: number): WindowLayoutNode {
  if (node.type === 'window') return node
  if (node.id === splitId) return { ...node, ratio: clampSplitRatio(ratio) }
  return {
    ...node,
    first: updateSplitRatio(node.first, splitId, ratio),
    second: updateSplitRatio(node.second, splitId, ratio),
  }
}

export function swapLayoutWindows(node: WindowLayoutNode, firstWindowId: string, secondWindowId: string): WindowLayoutNode {
  if (node.type === 'window') {
    if (node.windowId === firstWindowId) return { ...node, windowId: secondWindowId }
    if (node.windowId === secondWindowId) return { ...node, windowId: firstWindowId }
    return node
  }
  return {
    ...node,
    first: swapLayoutWindows(node.first, firstWindowId, secondWindowId),
    second: swapLayoutWindows(node.second, firstWindowId, secondWindowId),
  }
}

export function clampSplitRatio(value: number): number {
  return Math.min(maximumSplitRatio, Math.max(minimumSplitRatio, value))
}

function buildBalanced(windowIds: string[], prefix: string, direction: SplitDirection): WindowLayoutNode {
  if (windowIds.length === 1) return leafNode(`${prefix}-leaf`, windowIds[0])
  const middle = Math.ceil(windowIds.length / 2)
  const nextDirection = direction === 'horizontal' ? 'vertical' : 'horizontal'
  return splitNode(
    `${prefix}-split`,
    direction,
    middle / windowIds.length,
    buildBalanced(windowIds.slice(0, middle), `${prefix}-first`, nextDirection),
    buildBalanced(windowIds.slice(middle), `${prefix}-second`, nextDirection),
  )
}

function leafNode(id: string, windowId: string): WindowLayoutLeaf {
  return { type: 'window', id, windowId }
}

function splitNode(
  id: string,
  direction: SplitDirection,
  ratio: number,
  first: WindowLayoutNode,
  second: WindowLayoutNode,
): WindowLayoutSplit {
  return { type: 'split', id, direction, ratio: clampSplitRatio(ratio), first, second }
}

function visit(node: WindowLayoutNode, callback: (node: WindowLayoutNode) => void) {
  callback(node)
  if (node.type === 'split') {
    visit(node.first, callback)
    visit(node.second, callback)
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
