import type { WorkspaceState } from './workspace'

const channelName = 'stock-harness.workspace.sync.v1'

export type WorkspaceSync = {
  publish: (workspace: WorkspaceState) => void
  close: () => void
}

export function createWorkspaceSync(
  onWorkspace: (workspace: WorkspaceState) => void,
): WorkspaceSync {
  if (typeof BroadcastChannel === 'undefined') {
    return { publish: () => undefined, close: () => undefined }
  }
  const sourceId = crypto.randomUUID()
  const channel = new BroadcastChannel(channelName)
  channel.onmessage = event => {
    const message = event.data as { sourceId?: string; workspace?: WorkspaceState }
    if (message.sourceId === sourceId || !isWorkspaceState(message.workspace)) return
    onWorkspace(message.workspace)
  }
  return {
    publish: workspace => channel.postMessage({ sourceId, workspace }),
    close: () => channel.close(),
  }
}

function isWorkspaceState(value: unknown): value is WorkspaceState {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<WorkspaceState>
  return candidate.version === 3
    && typeof candidate.defaultGroupId === 'string'
    && typeof candidate.activeGroupId === 'string'
    && Array.isArray(candidate.groups)
    && candidate.groups.length > 0
}
