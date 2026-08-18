export type TrendRequestScope = {
  groupId: string
  windowId: string
  symbol: string
}

export type TrendRequestToken = TrendRequestScope & {
  generation: number
}

export function beginTrendRequest(
  generations: Map<string, number>,
  scope: TrendRequestScope,
): TrendRequestToken {
  const key = requestKey(scope)
  const generation = (generations.get(key) ?? 0) + 1
  generations.set(key, generation)
  return { ...scope, generation }
}

export function isCurrentTrendRequest(
  generations: Map<string, number>,
  token: TrendRequestToken,
  activeScope: TrendRequestScope | undefined,
): boolean {
  return activeScope !== undefined
    && activeScope.groupId === token.groupId
    && activeScope.windowId === token.windowId
    && activeScope.symbol === token.symbol
    && generations.get(requestKey(token)) === token.generation
}

function requestKey(scope: Pick<TrendRequestScope, 'groupId' | 'windowId'>): string {
  return `${scope.groupId}\u0000${scope.windowId}`
}
