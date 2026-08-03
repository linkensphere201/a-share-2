export type WindowAttachment = {
  id: string
  type: 'show-symbol' | 'show-members'
  sourceWindowId: string
  targetWindowId: string
}

export type AttachmentWindowDescriptor = {
  id: string
  type: 'instrument-list' | 'chart'
  mode?: 'attached' | 'detached'
}

export function validateWindowAttachments(
  attachments: WindowAttachment[],
  windows: AttachmentWindowDescriptor[],
): string[] {
  const windowById = new Map(windows.map(window => [window.id, window]))
  const attachmentIds = new Set<string>()
  const relationIds = new Set<string>()
  const issues: string[] = []

  for (const edge of attachments) {
    if (attachmentIds.has(edge.id)) issues.push(`duplicate attachment id: ${edge.id}`)
    attachmentIds.add(edge.id)
    const relationId = `${edge.type}:${edge.sourceWindowId}:${edge.targetWindowId}`
    if (relationIds.has(relationId)) issues.push(`duplicate attachment relation: ${relationId}`)
    relationIds.add(relationId)

    const source = windowById.get(edge.sourceWindowId)
    const target = windowById.get(edge.targetWindowId)
    if (!source) issues.push(`missing source window: ${edge.sourceWindowId}`)
    else if (source.type !== 'instrument-list') issues.push(`source is not a list: ${edge.sourceWindowId}`)
    if (!target) issues.push(`missing target window: ${edge.targetWindowId}`)
    else if (edge.type === 'show-symbol' && (target.type !== 'chart' || target.mode !== 'attached')) {
      issues.push(`show-symbol target is not an attached chart: ${edge.targetWindowId}`)
    } else if (edge.type === 'show-members' && (target.type !== 'instrument-list' || target.mode !== 'attached')) {
      issues.push(`show-members target is not an attached list: ${edge.targetWindowId}`)
    } else if (edge.type === 'show-members' && source?.mode !== 'detached') {
      issues.push(`show-members source is not a fixed list: ${edge.sourceWindowId}`)
    }
  }

  const targetsBySource = new Map<string, string[]>()
  for (const edge of attachments) {
    targetsBySource.set(edge.sourceWindowId, [...(targetsBySource.get(edge.sourceWindowId) ?? []), edge.targetWindowId])
  }
  const visited = new Set<string>()
  const activePath = new Set<string>()
  const findCycles = (windowId: string) => {
    if (activePath.has(windowId)) {
      issues.push(`attachment cycle at window: ${windowId}`)
      return
    }
    if (visited.has(windowId)) return
    activePath.add(windowId)
    for (const targetId of targetsBySource.get(windowId) ?? []) findCycles(targetId)
    activePath.delete(windowId)
    visited.add(windowId)
  }
  for (const sourceId of targetsBySource.keys()) findCycles(sourceId)
  return [...new Set(issues)]
}

export function removeWindowAttachments(attachments: WindowAttachment[], windowId: string): WindowAttachment[] {
  return attachments.filter(edge => edge.sourceWindowId !== windowId && edge.targetWindowId !== windowId)
}
