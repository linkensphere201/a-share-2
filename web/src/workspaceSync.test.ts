// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDefaultWorkspace } from './workspace'
import { createWorkspaceSync } from './workspaceSync'

class FakeBroadcastChannel {
  static channels: FakeBroadcastChannel[] = []
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(readonly name: string) {
    FakeBroadcastChannel.channels.push(this)
  }

  postMessage(data: unknown) {
    FakeBroadcastChannel.channels
      .filter(channel => channel !== this && channel.name === this.name)
      .forEach(channel => channel.onmessage?.({ data } as MessageEvent))
  }

  close() {
    FakeBroadcastChannel.channels = FakeBroadcastChannel.channels.filter(channel => channel !== this)
  }
}

afterEach(() => {
  FakeBroadcastChannel.channels = []
  vi.unstubAllGlobals()
})

describe('workspace cross-window synchronization', () => {
  it('publishes one logical workspace to other WebViews but not back to the sender', () => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const receivedByFirst: string[] = []
    const receivedBySecond: string[] = []
    const first = createWorkspaceSync(workspace => receivedByFirst.push(workspace.activeGroupId))
    const second = createWorkspaceSync(workspace => receivedBySecond.push(workspace.activeGroupId))

    first.publish(createDefaultWorkspace())

    expect(receivedByFirst).toEqual([])
    expect(receivedBySecond).toEqual(['group-primary'])
    first.close()
    second.close()
  })
})
