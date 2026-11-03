import { IconAlert, IconCheck, IconFolder } from '../../components/Icons'

interface Props {
  res: any
  fileName: string
  onRestart: () => void
  /** 选到同格式文件: 交回页面重跑识别(页面需要写 fBuf 并调 detect, 故整段上抛) */
  onFile: (file: File, buf: ArrayBuffer) => void
}

/** 第 4 步完成页。从 CleansingPage 外提: 只读 res 渲染统计与两个出口动作。 */
export default function ResultStep({ res, fileName, onRestart, onFile }: Props) {
  return (
    <div style={{ textAlign: 'center', padding: '20px 0' }}>
      {/* 2026-10-27: 图标 32px → 56px 圆形底(与看板空态同构, 语义色底) */}
      {(() => {
        const _ok = !res.async && res.success > 0
        const _pend = !!res.async
        const _bg = _pend
          ? 'rgba(0,122,255,0.12)'
          : _ok
            ? 'rgba(5,150,105,0.12)'
            : 'rgba(245,158,11,0.14)'
        const _c = _pend ? 'var(--primary)' : _ok ? 'var(--success)' : 'var(--warning)'
        return (
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: '50%',
              background: _bg,
              color: _c,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginBottom: 10,
            }}
          >
            {_ok || _pend ? <IconCheck size={30} /> : <IconAlert size={30} />}
          </div>
        )
      })()}
      {/* 标题语义化(原 res.message 直出) */}
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
        {res.async ? '已提交后台处理' : res.success > 0 ? '导入完成' : '导入未完成'}
      </div>
      {fileName ? (
        <div className="small muted" style={{ fontSize: 'var(--font-sm)', marginBottom: 8 }}>
          {fileName}
        </div>
      ) : (
        ''
      )}

      <div className="small muted" style={{ marginBottom: 16 }}>
        {res.error || res.message || ''}
        {res.error ? (
          <span style={{ marginLeft: 6, fontSize: 'var(--font-sm)', color: 'var(--warning)' }}>
            （侧边栏 <IconAlert size={12} style={{ display: 'inline', verticalAlign: 'middle' }} />{' '}
            查看详情）
          </span>
        ) : (
          ''
        )}
      </div>
      {res.async ? (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginBottom: 16 }}>
          <div>
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                color: 'var(--primary)',
                fontVariantNumeric: 'tabular-nums',
                lineHeight: 1.1,
              }}
            >
              {res.submitted}
            </div>
            <div className="small muted">已提交</div>
          </div>
          <div>
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                lineHeight: 1.1,
                color: 'var(--muted2)',
              }}
            >
              —
            </div>
            <div className="small muted">处理中</div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginBottom: 16 }}>
          <div>
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                color: 'var(--success)',
                fontVariantNumeric: 'tabular-nums',
                lineHeight: 1.1,
              }}
            >
              {res.success}
            </div>
            <div className="small muted">成功</div>
          </div>
          <div>
            <div
              style={{
                fontSize: 28,
                fontWeight: 700,
                lineHeight: 1.1,
                fontVariantNumeric: 'tabular-nums',
                color: res.failed > 0 ? 'var(--danger)' : 'var(--muted2)',
              }}
            >
              {res.failed}
            </div>
            <div className="small muted">跳过</div>
          </div>
        </div>
      )}
      {res.async && (
        <button
          className="btn btn-primary"
          style={{ marginTop: 14, marginBottom: 14 }}
          onClick={() => window.__setPage && window.__setPage('tasks')}
        >
          前往任务管理页查看进度 →
        </button>
      )}
      {res.failed > 0 && Array.isArray(res.failed_details) && res.failed_details.length > 0 && (
        <div
          style={{
            textAlign: 'left',
            background: 'var(--bg)',
            borderRadius: 'var(--radius-card)',
            padding: '10px 14px',
            marginBottom: 14,
            maxHeight: 180,
            overflowY: 'auto',
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 'var(--font-sm)',
              marginBottom: 6,
              color: 'var(--danger)',
            }}
          >
            失败明细（可对照修正后重新导入）
          </div>
          {res.failed_details.map((d, i) => (
            <div
              key={i}
              style={{
                fontSize: 'var(--font-xs)',
                color: 'var(--muted2)',
                padding: '3px 0',
                borderBottom: '1px solid var(--border)',
                display: 'flex',
                gap: 8,
              }}
            >
              <span className="mono" style={{ color: 'var(--text)', flexShrink: 0 }}>
                {d.sku || '-'}
              </span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.reason || ''}
              </span>
            </div>
          ))}
        </div>
      )}
      {/* 2026-10-27: 底部按钮统一 44px 胶囊(与首屏/映射/预览步骤一致) */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => {
            onRestart()
          }}
          className="clickable"
          style={{
            flex: '0 0 96px',
            minHeight: 44,
            borderRadius: 'var(--radius-full)',
            border: '1px solid var(--border)',
            background: 'var(--card)',
            color: 'var(--text)',
            fontSize: 'var(--font-md)',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          重新开始
        </button>
        <label
          style={{
            flex: 1,
            minHeight: 44,
            borderRadius: 'var(--radius-full)',
            border: 'none',
            background: 'var(--success)',
            color: '#fff',
            fontSize: 'var(--font-md)',
            fontWeight: 600,
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 5,
          }}
          className="clickable"
        >
          <IconFolder size={14} /> 导入相同格式
          <input
            type="file"
            accept=".csv,.xlsx"
            style={{ display: 'none' }}
            onChange={e => {
              const fi = e.target.files[0]
              if (fi) {
                const rd = new FileReader()
                rd.onload = () => {
                  const buf = rd.result as ArrayBuffer
                  onFile(fi, buf)
                }
                rd.readAsArrayBuffer(fi)
              }
            }}
          />
        </label>
      </div>
    </div>
  )
}
