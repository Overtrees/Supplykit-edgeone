import type { Dispatch, SetStateAction } from 'react'
import { IconCheck, IconClipboard, IconClose } from '../../components/Icons'

interface Props {
  colMap: Record<string, any[]>
  setColMap: Dispatch<SetStateAction<Record<string, any[]>>>
  colMapCol: string
  setColMapCol: Dispatch<SetStateAction<string>>
  colMapSaving: boolean
  channel: string
  onClose: () => void
  onSave: () => void
}

/** 表格列状态映射弹窗(底部 sheet)。
 *  从 CleansingPage 外提: 状态仍留在页面(受控组件), 这里只负责渲染与事件上抛。
 *  口径: 值→销量池/仅记录, 导入时后端按此归一化; 同名值后者覆盖前者(输入框红边警示)。 */
export default function ColMapSheet(props: Props) {
  const { colMap, setColMap, colMapCol, setColMapCol, colMapSaving, channel, onClose, onSave } =
    props
  return (
    <>
      <div
        onClick={() => onClose()}
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
            overflowY: 'auto',
          }}
        >
          <div
            style={{
              fontSize: 'var(--font-18)',
              fontWeight: 700,
              marginBottom: 12,
              textAlign: 'center',
            }}
          >
            表格列状态映射
          </div>
          <div
            style={{
              textAlign: 'center',
              fontSize: 'var(--font-xs)',
              color: 'var(--muted2)',
              marginBottom: 12,
            }}
          >
            按列配置 值→档位，导入时自动归一化 · 渠道：{channel === 'jd' ? '京东' : '其他渠道'}
            （全局主体隔离） 计入销量池=日销/GMV 都算；仅订单记录=写订单可见，GMV
            按状态（已发货类算），不计日销补货
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 'var(--font-13)', fontWeight: 600, flexShrink: 0 }}>
              映射列
            </span>
            <select
              value={colMapCol}
              onChange={e => setColMapCol(e.target.value)}
              style={{
                flex: 1,
                fontSize: 'var(--font-md)',
                padding: '8px 12px',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-lg)',
                background: 'var(--card)',
                outline: 'none',
              }}
            >
              <option value="order_status">订单状态（销量池判定）</option>
            </select>
          </div>
          {!(colMap[colMapCol] || []).length && (
            <div className="tpl-empty" style={{ padding: '12px 2px' }}>
              还没有配置：点击「补全内置默认」快速生成，或「+ 添加值」手动添加
            </div>
          )}
          {(colMap[colMapCol] || []).map((s, i) => (
            <div
              key={i}
              style={{
                // 2026-10-27 UX: 去外层胶囊框 + 去行分隔线; 加横向滚动(窄屏控件不挤压删除按钮贴边)
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '5px 0',
                overflowX: 'auto',
                WebkitOverflowScrolling: 'touch',
              }}
            >
              <input
                value={s.name || ''}
                onChange={e =>
                  setColMap(p => ({
                    ...p,
                    [colMapCol]: (p[colMapCol] || []).map((x, j) =>
                      j === i ? { ...x, name: e.target.value } : x,
                    ),
                  }))
                }
                placeholder="值(如 交易成功)"
                className="tpl-field"
                title={
                  (colMap[colMapCol] || []).filter(x => x.name && x.name === s.name).length > 1
                    ? '值重复：导入归一化时后者覆盖前者'
                    : ''
                }
                style={{
                  flex: '1 0 100px',
                  minWidth: 100,
                  // 2026-10-27 重复值校验: 同列表重名红边警示(归一化 dict 后者覆盖, 结果不确定)
                  borderColor:
                    s.name && (colMap[colMapCol] || []).filter(x => x.name === s.name).length > 1
                      ? 'var(--danger)'
                      : undefined,
                }}
              />
              {/* 2026-10-27 emoji→SVG: <option> 无法内嵌 SVG, 改分段控件(两态 pill, 语义图标+文字) */}
              <div
                style={{
                  display: 'flex',
                  gap: 2,
                  padding: 2,
                  borderRadius: 99,
                  background: 'var(--bg)',
                  flexShrink: 0,
                }}
              >
                {(
                  [
                    {
                      v: 'sale',
                      Icon: IconCheck,
                      label: '销量池',
                      tip: '计入销量池（日销+GMV）',
                    },
                    {
                      v: 'blocked',
                      Icon: IconClipboard,
                      label: '仅记录',
                      tip: '仅订单记录（GMV 按状态，不计日销）',
                    },
                  ] as const
                ).map(({ v, Icon, label, tip }) => {
                  const act = (s.group || 'blocked') === v
                  return (
                    <span
                      key={v}
                      title={tip}
                      className="clickable"
                      onClick={() =>
                        setColMap(p => ({
                          ...p,
                          [colMapCol]: (p[colMapCol] || []).map((x, j) =>
                            j === i ? { ...x, group: v } : x,
                          ),
                        }))
                      }
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        padding: '7px 10px',
                        borderRadius: 99,
                        fontSize: 'var(--font-12)',
                        fontWeight: act ? 600 : 400,
                        whiteSpace: 'nowrap',
                        cursor: 'pointer',
                        background: act ? 'var(--card)' : 'transparent',
                        color: act
                          ? v === 'sale'
                            ? 'var(--success)'
                            : 'var(--muted)'
                          : 'var(--muted2)',
                        boxShadow: act ? 'var(--inset-glass)' : 'none',
                      }}
                    >
                      <Icon size={12} />
                      {label}
                    </span>
                  )
                })}
              </div>
              <button
                onClick={() =>
                  setColMap(p => ({
                    ...p,
                    [colMapCol]: (p[colMapCol] || []).filter((_, j) => j !== i),
                  }))
                }
                className="clickable tpl-btn tpl-btn-ghost"
                style={{ color: 'var(--danger)', width: 40, padding: 0, flexShrink: 0 }}
              >
                <IconClose size={16} />
              </button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
            <button
              onClick={() =>
                setColMap(p => ({
                  ...p,
                  [colMapCol]: [...(p[colMapCol] || []), { name: '', group: 'blocked' }],
                }))
              }
              className="btn btn-ghost clickable"
              style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
            >
              + 添加值
            </button>
            <button
              onClick={() =>
                setColMap(p => {
                  // 2026-10-27 合并填充(曾整表覆盖——用户手工配置全丢): 保留已有, 仅补缺失内置项
                  const _def = [
                    { name: '已完成', group: 'sale' },
                    { name: '交易成功', group: 'sale' },
                    { name: '确认收货', group: 'sale' },
                    { name: '已签收', group: 'sale' },
                    { name: '妥投', group: 'sale' },
                    { name: 'Closed', group: 'sale' },
                    { name: 'Completed', group: 'sale' },
                    { name: '待发货', group: 'blocked' },
                    { name: '已发货', group: 'blocked' },
                    { name: '待确认', group: 'blocked' },
                    { name: '待付款', group: 'blocked' },
                    { name: '已取消', group: 'blocked' },
                    { name: '已退款', group: 'blocked' },
                    { name: '退款中', group: 'blocked' },
                    { name: '申请退款', group: 'blocked' },
                    { name: '已退货', group: 'blocked' },
                    { name: '运输中', group: 'blocked' },
                    { name: '在途', group: 'blocked' },
                  ]
                  const _cur: any[] = (p as any).order_status || []
                  const _names = new Set(_cur.map((x: any) => x.name))
                  const _merged = [..._cur, ..._def.filter((d: any) => !_names.has(d.name))]
                  return { ...p, order_status: _merged }
                })
              }
              className="btn btn-ghost clickable"
              style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
            >
              补全内置默认
            </button>
            <button
              onClick={() =>
                setColMap(p => ({
                  ...p,
                  [colMapCol]: (p[colMapCol] || []).map((x: any) => ({ ...x, group: 'sale' })),
                }))
              }
              className="btn btn-ghost clickable"
              style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
            >
              全部销量池
            </button>
            <button
              onClick={() =>
                setColMap(p => ({
                  ...p,
                  [colMapCol]: (p[colMapCol] || []).map((x: any) => ({ ...x, group: 'blocked' })),
                }))
              }
              className="btn btn-ghost clickable"
              style={{ fontSize: 'var(--font-sm)', padding: '7px 14px', minHeight: 36 }}
            >
              全部仅记录
            </button>
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <div onClick={() => onClose()} className="clickable sheet-cancel" style={{ flex: 1 }}>
              <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>取消</span>
            </div>
            <div
              onClick={() => {
                if (!colMapSaving) onSave()
              }}
              className="clickable sheet-close"
              style={{ flex: 1, opacity: colMapSaving ? 0.6 : 1 }}
            >
              <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>
                {colMapSaving ? '保存中...' : '保存'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
