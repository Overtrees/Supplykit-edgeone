import React, { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'

/** 日志文件列表(iPhone 日志分析式): 按天平铺 log_archives 归档, 点击行 → 底部弹窗预览当日 md, 系统分享 API 导出
 * 用于: 质量日志页直接平铺(scope=user) + 开发者模式日志弹窗(scope=dev) */
export default function LogFileList({ scope = 'user' }: { scope?: string }) {
  const [files, setFiles] = useState<any[]>([])
  const [ld, setLd] = useState(true)
  const [preview, setPreview] = useState<any>(null) // {date, markdown, count, loading} — 底部弹窗
  const [sharing, setSharing] = useState(false)

  const load = () => {
    setLd(true)
    api
      .get('/api/quality-logs/files?days=30&scope=' + scope)
      .then((r: any) => {
        setFiles(r.data?.items || [])
        setLd(false)
      })
      .catch(() => {
        setLd(false)
      })
  }
  useEffect(() => {
    load() /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [scope])

  // 2026-10-28 根治'弹窗加载不出需退出重进': dev 大文件(666 条)聚合 2.7s+, 曾撞 30s 超时/挂起 inflight
  // → ① 超时 90s ② 失败自动重试 1 次(跳过缓存) ③ 错误态可点击重试
  const openPreview = async (date: string, retry = 0) => {
    setPreview({ date, markdown: '', count: 0, loading: true })
    try {
      const r = await api.get(
        '/api/quality-logs/file?date=' +
          date +
          '&scope=' +
          scope +
          (retry ? '&t=' + Date.now() : ''),
        { timeout: 90000 },
      )
      setPreview({
        date,
        markdown: r.data?.markdown || '# 无日志\n',
        count: r.data?.count || 0,
        loading: false,
      })
    } catch (e) {
      if (retry < 1) {
        setTimeout(() => openPreview(date, retry + 1), 800)
        return
      }
      setPreview({
        date,
        markdown: '加载失败，点此重试',
        count: 0,
        loading: false,
        error: true,
      })
    }
  }

  const shareFile = async () => {
    if (!preview || sharing) return
    setSharing(true)
    const name = `quality-logs-${preview.date}-${scope}.md`
    const file = new File([preview.markdown], name, { type: 'text/markdown;charset=utf-8' })
    const nav = navigator as any
    if (nav.share && nav.canShare && nav.canShare({ files: [file] })) {
      try {
        await nav.share({ files: [file], title: name })
        setSharing(false)
        return
      } catch (e) {
        /* 用户取消 */
      }
    }
    // fallback: 下载
    const url = URL.createObjectURL(file)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    setSharing(false)
  }

  return (
    <div>
      {ld ? (
        <div
          style={{
            padding: 20,
            textAlign: 'center',
            color: 'var(--muted2)',
            fontSize: 'var(--font-sm)',
          }}
        >
          加载文件列表...
        </div>
      ) : files.length === 0 ? (
        <div
          style={{
            padding: 20,
            textAlign: 'center',
            color: 'var(--muted2)',
            fontSize: 'var(--font-sm)',
          }}
        >
          暂无历史日志文件（每日维护自动归档，保留 90 天）
        </div>
      ) : (
        files.map((f: any, i: number) => (
          <div
            key={f.date || i}
            onClick={() => openPreview(f.date)}
            style={{
              padding: '10px 12px',
              borderBottom: '1px solid var(--border)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              cursor: 'pointer',
            }}
          >
            <span
              style={{
                fontSize: 'var(--font-13)',
                fontWeight: 600,
                fontVariantNumeric: 'tabular-nums',
                flexShrink: 0,
              }}
            >
              {String(f.date || '').replace(/-/g, '.')}
            </span>
            <span
              className="pill info"
              style={{
                fontSize: 'var(--font-10)',
                padding: '1px 8px',
                minHeight: 'auto',
                lineHeight: '18px',
                flexShrink: 0,
              }}
            >
              {f.count || 0} 条
            </span>
            <span
              style={{ marginLeft: 'auto', color: 'var(--muted2)', fontSize: 'var(--font-sm)' }}
            >
              ›
            </span>
          </div>
        ))
      )}

      {/* 预览底部弹窗(标准 sheet, createPortal 脱离弹窗容器——嵌套弹窗被 overflow 裁剪会点击无反应) */}
      {preview &&
        createPortal(
          <>
            <div
              onClick={() => setPreview(null)}
              style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
            />
            <div
              style={{
                position: 'fixed',
                left: 0,
                right: 0,
                bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
                zIndex: 9999,
                display: 'flex',
                justifyContent: 'center',
                padding: '0 14px',
                pointerEvents: 'none',
              }}
            >
              <div
                onClick={e => e.stopPropagation()}
                className="material-regular"
                style={{
                  width: '100%',
                  maxWidth: 600,
                  borderRadius: 'var(--radius-lg)',
                  padding: '18px 14px calc(14px + env(safe-area-inset-bottom))',
                  boxShadow: 'var(--shadow-sheet)',
                  pointerEvents: 'auto',
                  maxHeight: '70vh',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <div
                  style={{
                    fontSize: 'var(--font-18)',
                    fontWeight: 700,
                    marginBottom: 4,
                    textAlign: 'center',
                    color: 'var(--text)',
                  }}
                >
                  质量日志 · {preview.date}{' '}
                  <span
                    style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', fontWeight: 400 }}
                  >
                    {preview.count} 条
                  </span>
                </div>
                {preview.loading ? (
                  <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted2)' }}>
                    加载中...
                  </div>
                ) : (
                  <>
                    <pre
                      style={{
                        flex: 1,
                        overflowY: 'auto',
                        margin: '8px 0',
                        padding: 10,
                        background: 'var(--bg)',
                        borderRadius: 'var(--radius-md)',
                        fontSize: 'var(--font-10)',
                        lineHeight: 1.6,
                        color: 'var(--text)',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                      }}
                    >
                      {preview.markdown}
                    </pre>
                    {preview.error && (
                      <button
                        onClick={() => openPreview(preview.date)}
                        className="btn btn-primary"
                        style={{ width: '100%', minHeight: 40, marginBottom: 8 }}
                      >
                        重试加载
                      </button>
                    )}
                    <button
                      onClick={shareFile}
                      disabled={sharing}
                      className="btn btn-primary"
                      style={{
                        width: '100%',
                        minHeight: 40,
                        fontSize: 'var(--font-md)',
                        flexShrink: 0,
                      }}
                    >
                      {sharing ? '分享中...' : '分享文件'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </>,
          document.body,
        )}
    </div>
  )
}
