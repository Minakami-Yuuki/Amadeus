import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useBackend } from '../hooks/useBackend'

// Standalone always-on-top text box. The conversation itself is rendered by the
// wallpaper / avatar surfaces, so this window only captures a line of text and
// hands it to the same chat pipeline the control panel uses.
const SESSION_STORAGE_KEY = 'amadeus.chatInput.sessionId'
// While another application covers the wallpaper display the reply would be
// invisible, so the avatar panel stands in for it; it falls back to the
// wallpaper after one quiet minute.
const COMPANION_IDLE_MS = 60 * 1000
const COMPANION_IDLE_POLL_MS = 10 * 1000
// Occlusion is measured against the CRT conversation rect, not the monitor, so a
// window parked elsewhere on that screen no longer counts as covering it.
const AREA_OCCLUSION_RATIO = 0.2

type DisplayRect = { x: number; y: number; width: number; height: number }
type WindowRect = { left: number; top: number; width: number; height: number }

function coversDisplay(rect: WindowRect, area: DisplayRect): boolean {
  const right = rect.left + rect.width
  const bottom = rect.top + rect.height
  const left = Math.max(rect.left, area.x)
  const top = Math.max(rect.top, area.y)
  const clipRight = Math.min(right, area.x + area.width)
  const clipBottom = Math.min(bottom, area.y + area.height)
  if (clipRight <= left || clipBottom <= top) return false
  return (clipRight - left) * (clipBottom - top) >= area.width * area.height * AREA_OCCLUSION_RATIO
}

export default function ChatInputBar() {
  const { send, subscribe, connected } = useBackend()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [balance, setBalance] = useState<{ total: number; currency: string } | null>(null)
  const [balanceError, setBalanceError] = useState('')
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const sessionRef = useRef('')
  const lastActivityRef = useRef(Date.now())
  const probingRef = useRef(false)
  const submittingRef = useRef(false)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flashNotice = useCallback((message: string) => {
    setNotice(message)
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = setTimeout(() => { setNotice(''); noticeTimerRef.current = null }, 3000)
  }, [])

  useEffect(() => () => {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current)
  }, [])

  const refreshBalance = useCallback(async () => {
    try {
      const result = await window.amadeus?.getDeepseekBalance()
      if (result?.ok && typeof result.total === 'number') {
        setBalance({ total: result.total, currency: String(result.currency || 'CNY') })
        setBalanceError('')
      } else {
        setBalance(null)
        setBalanceError(String(result?.error || 'unavailable'))
      }
    } catch {
      setBalanceError('unavailable')
    }
  }, [])

  useEffect(() => {
    void refreshBalance()
    const timer = setInterval(() => { void refreshBalance() }, 5 * 60 * 1000)
    return () => clearInterval(timer)
  }, [refreshBalance])

  useEffect(() => {
    inputRef.current?.focus()
  }, [connected])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        void window.amadeus?.hideChatInput()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // Transparent pixels still belong to the window and would swallow clicks meant
  // for whatever sits underneath, so pass them through while the pointer is off
  // the bar itself.
  const clickThroughRef = useRef(true)
  useEffect(() => {
    void window.amadeus?.setChatInputClickThrough(true)
    const sync = (ignore: boolean) => {
      if (clickThroughRef.current === ignore) return
      clickThroughRef.current = ignore
      void window.amadeus?.setChatInputClickThrough(ignore)
    }
    const onMove = (event: MouseEvent) => {
      const element = document.elementFromPoint(event.clientX, event.clientY)
      sync(!(element && element.closest('[data-chat-bar]')))
    }
    window.addEventListener('mousemove', onMove)
    return () => {
      window.removeEventListener('mousemove', onMove)
      void window.amadeus?.setChatInputClickThrough(false)
    }
  }, [])

  const noteActivity = useCallback(() => { lastActivityRef.current = Date.now() }, [])

  useEffect(() => {
    const timer = setInterval(() => {
      if (Date.now() - lastActivityRef.current < COMPANION_IDLE_MS) return
      void window.amadeus?.autoHideCompanion()
    }, COMPANION_IDLE_POLL_MS)
    return () => clearInterval(timer)
  }, [])

  // Someone else's window covering the wallpaper display means the reply has
  // nowhere to show, so raise the avatar panel for this turn.
  const maybeShowCompanion = useCallback(async () => {
    if (probingRef.current) return
    probingRef.current = true
    try {
      const area = (await window.amadeus?.getConversationBounds())
        || (await window.amadeus?.getWallpaperBounds())
      if (!area) return
      const listed = await send('system.list_windows', { limit: 60 })
      const windows = Array.isArray(listed.windows)
        ? listed.windows as Array<Record<string, unknown>>
        : []
      const blocked = windows.some((entry) => {
        const rect = entry.rect as Record<string, unknown> | undefined
        if (!rect) return false
        const left = Number(rect.left)
        const top = Number(rect.top)
        const width = Number(rect.width)
        const height = Number(rect.height)
        if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return false
        const name = String(entry.processName || '').toLowerCase()
        if (!name) return false
        if (name.includes('electron') || name.includes('amadeus') || name.includes('lively')) return false
        if (name.includes('explorer') || name.includes('searchhost') || name.includes('shellexperiencehost')) return false
        return coversDisplay({ left, top, width, height }, area)
      })
      if (blocked) await window.amadeus?.autoShowCompanion()
    } catch (error) {
      console.error('[chat-input] companion probe failed', error)
    } finally {
      probingRef.current = false
    }
  }, [send])

  // Occlusion decides, not the trigger: every spoken line (chat reply, AUIP
  // narration, idle-chatter opener) re-checks whether the wallpaper display is
  // covered right now. Covered → raise the avatar panel; otherwise the wallpaper
  // already shows it and the panel stays closed. maybeShowCompanion() itself is
  // single-flight, so back-to-back lines cannot race.
  useEffect(() => {
    const lastProbeAtRef = { current: 0 }
    const probe = (force: boolean) => {
      const now = Date.now()
      // Streaming emits a token every few hundred ms; enumerating every window
      // that often is pointless. The turn end always re-probes.
      if (!force && now - lastProbeAtRef.current < 3000) return
      lastProbeAtRef.current = now
      void maybeShowCompanion()
    }
    const onSpeech = () => {
      noteActivity()
      probe(false)
    }
    const offToken = subscribe('chat.token', onSpeech)
    const offComplete = subscribe('chat.complete', () => {
      noteActivity()
      probe(true)
    })
    const offSubtitle = subscribe('render.subtitle', onSpeech)
    return () => { offToken(); offComplete(); offSubtitle() }
  }, [maybeShowCompanion, noteActivity, subscribe])

  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionRef.current) return sessionRef.current
    const cached = localStorage.getItem(SESSION_STORAGE_KEY) || ''
    try {
      const listed = await send('session.list', {})
      const sessions = Array.isArray(listed.sessions) ? listed.sessions as Array<{ id?: string }> : []
      const ids = sessions.map(session => String(session.id || '')).filter(Boolean)
      const chosen = ids.includes(cached) ? cached : (ids[0] || '')
      if (chosen) {
        sessionRef.current = chosen
        localStorage.setItem(SESSION_STORAGE_KEY, chosen)
        return chosen
      }
    } catch {
      /* offline; fall through and let the send create the session */
    }
    try {
      const created = await send('session.create', {})
      const id = String((created.session as { id?: string } | undefined)?.id || '')
      sessionRef.current = id
      if (id) localStorage.setItem(SESSION_STORAGE_KEY, id)
      return id
    } catch {
      return ''
    }
  }, [send])

  const startNewSession = useCallback(async () => {
    if (busy || submittingRef.current) return
    try {
      const created = await send('session.create', {})
      const id = String((created.session as { id?: string } | undefined)?.id || '')
      sessionRef.current = id
      if (id) localStorage.setItem(SESSION_STORAGE_KEY, id)
      setText('')
      flashNotice('已开启新对话')
      noteActivity()
      inputRef.current?.focus()
    } catch (error) {
      console.error('[chat-input] new session failed', error)
      flashNotice('新建对话失败')
    }
  }, [busy, flashNotice, noteActivity, send])

  const submit = useCallback(async () => {
    const value = text.trim()
    // A ref guard, not the busy state: two Enter presses in the same tick both see
    // busy === false before React re-renders, which used to send twice.
    if (!value || submittingRef.current) return
    submittingRef.current = true
    setBusy(true)
    setNotice('')
    setText('')
    noteActivity()
    try {
      const sessionId = await ensureSession()
      await send('chat.send', {
        text: value,
        provider: 'deepseek',
        turn_id: crypto.randomUUID(),
        session_id: sessionId,
      })
      await maybeShowCompanion()
    } catch (error) {
      setText(value)
      setNotice('发送失败：后端未连接')
      console.error('[chat-input] send failed', error)
    } finally {
      submittingRef.current = false
      setBusy(false)
      inputRef.current?.focus()
    }
  }, [busy, ensureSession, maybeShowCompanion, noteActivity, send, text])

  const handleKeyDown = useCallback((event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }, [submit])

  return (
    <div style={styles.wrapper}>
      <div style={styles.bar} data-chat-bar="1">
        <div style={styles.grip} title="拖动">
          <span style={styles.dot} />
          <span style={styles.dot} />
          <span style={styles.dot} />
        </div>
        <button
          type="button"
          onClick={() => { void startNewSession() }}
          style={styles.newChat}
          title="开启新对话"
          disabled={busy}
        >
          ＋
        </button>
        <textarea
          ref={inputRef}
          value={text}
          onChange={event => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={connected ? '和红莉栖说点什么…（Enter 发送 / Shift+Enter 换行）' : '后端连接中…'}
          style={styles.input}
          spellCheck={false}
        />
        <button
          type="button"
          onClick={() => { void refreshBalance() }}
          style={styles.balance}
          title={balance
            ? `DeepSeek 余额 ${balance.total.toFixed(2)} ${balance.currency}（点击刷新）`
            : `余额不可用${balanceError ? `：${balanceError}` : ''}（点击刷新）`}
        >
          {balance ? `${balance.currency === 'CNY' ? '¥' : ''}${balance.total.toFixed(2)}` : '¥--'}
        </button>
        <span
          title={connected ? '已连接' : '未连接'}
          style={{ ...styles.status, background: connected ? '#4ade80' : '#f87171' }}
        />
        <button
          type="button"
          onClick={() => { void submit() }}
          disabled={busy || !text.trim()}
          style={{ ...styles.send, opacity: busy || !text.trim() ? 0.45 : 1 }}
          title="发送"
        >
          {busy ? '···' : '↵'}
        </button>
        <button
          type="button"
          onClick={() => { void window.amadeus?.hideChatInput() }}
          style={styles.close}
          title="隐藏（Ctrl+Shift+A 可再次呼出）"
        >
          ×
        </button>
      </div>
      {notice ? <div style={styles.notice}>{notice}</div> : null}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    padding: 8,
    boxSizing: 'border-box',
    font: '13px/1.4 "Segoe UI", "Microsoft YaHei", sans-serif',
  },
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 10px',
    borderRadius: 16,
    background: 'rgba(22, 18, 28, 0.86)',
    border: '1px solid rgba(255,255,255,0.12)',
  },
  grip: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    padding: '2px 4px',
    cursor: 'grab',
    WebkitAppRegion: 'drag',
  } as CSSProperties,
  dot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    background: 'rgba(255,255,255,0.35)',
  },
  input: {
    flex: 1,
    minHeight: 20,
    maxHeight: 56,
    resize: 'none',
    border: 'none',
    outline: 'none',
    background: 'transparent',
    color: '#f4eef7',
    font: '13px/1.4 "Segoe UI", "Microsoft YaHei", sans-serif',
    WebkitAppRegion: 'no-drag',
  } as CSSProperties,
  status: {
    width: 7,
    height: 7,
    borderRadius: 4,
    flex: '0 0 auto',
  },
  newChat: {
    flex: '0 0 auto',
    width: 24,
    height: 24,
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.14)',
    cursor: 'pointer',
    background: 'transparent',
    color: 'rgba(255,255,255,0.72)',
    fontSize: 13,
    lineHeight: 1,
  },
  balance: {
    flex: '0 0 auto',
    border: 'none',
    background: 'transparent',
    cursor: 'pointer',
    color: 'rgba(255,255,255,0.55)',
    font: '11px/1 "Segoe UI", "Microsoft YaHei", sans-serif',
    padding: '2px 4px',
  },
  send: {
    flex: '0 0 auto',
    width: 26,
    height: 26,
    borderRadius: 8,
    border: 'none',
    cursor: 'pointer',
    background: 'rgba(196, 76, 104, 0.85)',
    color: '#fff',
    fontSize: 13,
  },
  close: {
    flex: '0 0 auto',
    width: 22,
    height: 22,
    borderRadius: 6,
    border: 'none',
    cursor: 'pointer',
    background: 'transparent',
    color: 'rgba(255,255,255,0.55)',
    fontSize: 15,
    lineHeight: 1,
  },
  notice: {
    color: '#f87171',
    fontSize: 11,
    paddingLeft: 10,
  },
}
