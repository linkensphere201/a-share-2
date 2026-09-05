import { useEffect, useState } from 'react'
import { Plus, Save, Tag, X } from 'lucide-react'
import { InstrumentBrowser } from './InstrumentBrowser'
import type { Instrument } from './workspace'

const suggestedTags = [
  '板块核心辨识度', '情绪弹性核心', '容量核心', '中军', '补涨后排',
]
const maxTags = 8
const maxTagLength = 20

export function InstrumentTagManager() {
  const [selected, setSelected] = useState<Instrument>()
  const [tags, setTags] = useState<string[]>([])
  const [savedTags, setSavedTags] = useState<string[]>([])
  const [customTag, setCustomTag] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    fetch(`/api/instruments/${encodeURIComponent(selected.symbol)}`, {
      signal: controller.signal,
    }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<Instrument>
    }).then(instrument => {
      const next = instrument.instrument_tags ?? []
      setSelected(instrument)
      setTags(next)
      setSavedTags(next)
    }).catch(reason => {
      if ((reason as Error).name !== 'AbortError') setError('标签加载失败')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [selected?.symbol])

  const toggleTag = (tag: string) => {
    setError('')
    setTags(current => current.includes(tag)
      ? current.filter(item => item !== tag)
      : current.length < maxTags ? [...current, tag] : current)
  }

  const addCustomTag = () => {
    const tag = customTag.trim()
    if (!tag) return
    if (tag.length > maxTagLength) {
      setError(`单个标签不能超过 ${maxTagLength} 个字符`)
      return
    }
    if (tags.some(item => item.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
      setCustomTag('')
      return
    }
    if (tags.length >= maxTags) {
      setError(`每个标的最多 ${maxTags} 个标签`)
      return
    }
    setTags(current => [...current, tag])
    setCustomTag('')
    setError('')
  }

  const save = async () => {
    if (!selected || saving) return
    setSaving(true)
    setError('')
    try {
      const response = await fetch(
        `/api/instruments/${encodeURIComponent(selected.symbol)}/tags`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tags }),
        },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const body = await response.json() as { tags: string[] }
      setTags(body.tags)
      setSavedTags(body.tags)
      setSelected(current => current ? { ...current, instrument_tags: body.tags } : current)
      window.dispatchEvent(new CustomEvent('stock-harness:instrument-tags-changed', {
        detail: { symbol: selected.symbol },
      }))
    } catch {
      setError('标签保存失败')
    } finally {
      setSaving(false)
    }
  }

  const dirty = JSON.stringify(tags) !== JSON.stringify(savedTags)

  return <div className="instrument-tag-manager">
    <InstrumentBrowser
      selectedSymbols={new Set()}
      onSelect={setSelected}
      stockOnly
      actionMode="edit-tags"
      searchLabel="搜索要编辑标签的股票"
      placeholder="搜索股票代码、名称或拼音"
    />
    <section className="instrument-tag-editor" aria-label="标的标签设置">
      {!selected && <div className="instrument-editor-empty">从左侧搜索结果中选择一只股票</div>}
      {selected && <>
        <header><span><Tag size={15}/><strong>{selected.name}</strong><small>{selected.symbol}</small></span></header>
        {loading ? <div className="instrument-editor-empty">正在加载标签</div> : <>
          <div className="instrument-tag-suggestions">
            {suggestedTags.map(tag => <button
              key={tag}
              className={tags.includes(tag) ? 'active' : ''}
              onClick={() => toggleTag(tag)}
            >{tag}</button>)}
          </div>
          <div className="instrument-tag-current">
            {tags.length === 0 && <small>未设置标签，该标的按普通标的展示</small>}
            {tags.map(tag => <span key={tag}>{tag}<button title={`移除 ${tag}`} aria-label={`移除 ${tag}`} onClick={() => toggleTag(tag)}><X size={11}/></button></span>)}
          </div>
          <div className="instrument-tag-custom">
            <input
              value={customTag}
              maxLength={maxTagLength}
              aria-label="自定义标的标签"
              placeholder="输入自定义标签"
              onChange={event => setCustomTag(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  addCustomTag()
                }
              }}
            />
            <button className="command-button" disabled={!customTag.trim()} onClick={addCustomTag}><Plus size={13}/>添加</button>
          </div>
          {error && <div className="instrument-tag-error">{error}</div>}
          <footer>
            <span>{tags.length}/{maxTags}{dirty ? ' · 有未保存改动' : ' · 已保存'}</span>
            <button className="primary-button" disabled={!dirty || saving} onClick={save}><Save size={13}/>{saving ? '保存中' : '保存标签'}</button>
          </footer>
        </>}
      </>}
    </section>
  </div>
}
