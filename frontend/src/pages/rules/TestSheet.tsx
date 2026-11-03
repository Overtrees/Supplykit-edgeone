import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { api, apiJson } from '../../api/client'
import { useToast } from '../../components/Toast'
import { IconCheck, IconClose, IconGear, IconLightning, IconLoading } from '../../components/Icons'
import { IS } from './shared'
import { fieldLbl, opLbl } from './labels'

const API = import.meta.env.VITE_API_BASE_URL || ''

/**
 * 规则测试弹窗(可视化调试: 输入模拟数据判断是否触发) —— 自 RulesPage.tsx 外提(S3-1 续)。
 * 全部测试状态(testInv/testResult/testParams/testOwn/testBatch/testSampleSku/testLoading)
 * 与 runTest 随迁; 父级仅保留 testRule(待测规则对象)作为开关。
 * 打开时按 rule 上附带的 __testParams/__testInv 初始化(编辑表单"测试"按钮打包写入),
 * 与原实现一致: 每次打开重置 inv/params/result, own/batch/样本SKU 保留上次输入。
 */
export default function RuleTestSheet({
  rule,
  onClose,
  globalChannel,
}: {
  rule: any
  onClose: () => void
  globalChannel: string
}) {
  const toast = useToast()
  // 库存试算输入(可编辑回填): 数值字段编辑期变字符串, 后端回填 adj_dos/buffer/health_score
  interface TestInv {
    available_qty?: number | string
    safety_qty?: number | string
    in_transit_qty?: number | string
    warehouse_type?: string
    days_since_last?: number | string
    order_quantity?: number | string
    adj_dos?: number | string
    buffer?: number | string
    health_score?: number | string
    [k: string]: unknown
  }
  const [testInv, setTestInv] = useState<TestInv>({
    available_qty: 0,
    safety_qty: 0,
    in_transit_qty: 0,
    warehouse_type: '',
    days_since_last: 0,
    order_quantity: 0,
  })
  const [testResult, setTestResult] = useState(null)
  const [testParams, setTestParams] = useState({})
  // 2026-11-01 自有仓供给侧/效期 模拟值(与后端 body.own / body.batch 对应)
  const [testOwn, setTestOwn] = useState<Record<string, string | number>>({
    available: 0,
    transit: 0,
    po_transit: 0,
    can_ship: 0,
    downstream_ds: 10,
    downstream_avail: 0,
    b2c_days: 6,
    need: '',
    gap: '',
    cover_days: '',
  })
  const [testBatch, setTestBatch] = useState<Record<string, string | number>>({
    days_to_expire: 10,
  })
  const [testSampleSku, setTestSampleSku] = useState('')
  const [testLoading, setTestLoading] = useState(false)

  // 打开(或换规则)时: 用编辑表单打包的初始值重置 inv/params/result(own/batch/样本SKU 保留)
  useEffect(() => {
    if (!rule) return
    setTestInv(rule.__testInv || {})
    setTestParams(rule.__testParams || {})
    setTestResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rule])

  const runTest = async () => {
    if (!rule) return
    setTestLoading(true)
    try {
      // S3-2 收口: 测试是重操作, 超时对齐 postHeavy 90s
      const d = await apiJson('POST', API + '/api/rules/' + rule.id + '/test', {
        timeout: 90000,
        data: {
          inv: {
            available_qty: Number(testInv.available_qty) || 0,
            safety_qty: Number(testInv.safety_qty) || 0,
            in_transit_qty: Number(testInv.in_transit_qty) || 0,
            warehouse_type: testInv.warehouse_type,
            days_since_last: Number(testInv.days_since_last) || 0,
            adj_dos: Number(testInv.adj_dos) || 0,
            buffer: Number(testInv.buffer) || 0,
          },
          order: { quantity: Number(testInv.order_quantity) || 0 },
          params: testParams,
          // 2026-11-01 own./batch. 模拟值透传 —— 不传后端也会给默认, 但用户需要能改数观察触发
          own: (() => {
            const o: Record<string, number> = {}
            for (const k of Object.keys(testOwn)) {
              const v = testOwn[k]
              if (v === '' || v === undefined || v === null) continue
              const n = Number(v)
              if (!isNaN(n)) o[k] = n
            }
            // need/gap/cover_days 留空时由后端按下游日销×周期 / 可用÷日销 推导, 不硬塞 0
            return o
          })(),
          batch: (() => {
            const v = testBatch.days_to_expire
            return v === '' || v === undefined || v === null
              ? undefined
              : { days_to_expire: Number(v) }
          })(),
          health: {
            score:
              testInv.health_score === undefined || testInv.health_score === ''
                ? 100
                : Number(testInv.health_score),
          },
        },
      })
      if (d.ok) setTestResult(d.data)
      else toast.error('测试失败: ' + (d.error || ''))
    } catch (e) {
      toast.error('测试失败: ' + e.message)
    }
    setTestLoading(false)
  }

  if (!rule) return null
  return createPortal(
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'transparent', zIndex: 9998 }}
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
              alignSelf: 'center',
              background: 'var(--glass-bg)',
              backdropFilter: 'var(--blur-regular)',
              WebkitBackdropFilter: 'var(--blur-regular)',
              borderRadius: 'var(--radius-full)',
              padding: '7px 16px',
              marginBottom: 12,
              textAlign: 'center',
              fontSize: 'var(--font-18)',
              fontWeight: 700,
              color: 'var(--text)',
              maxWidth: '100%',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            规则测试
          </div>
          <div style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>
            <div
              style={{
                textAlign: 'center',
                fontSize: 'var(--font-sm)',
                color: 'var(--muted2)',
                marginBottom: 10,
              }}
            >
              {rule.name}
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 10, alignItems: 'center' }}>
              <button
                onClick={async () => {
                  try {
                    const r = await api.get('/api/inventory/sample?channel=' + globalChannel)
                    const d = r.data || {}
                    if (d.sku) {
                      setTestInv(p => ({
                        ...p,
                        available_qty: d.available_qty || 0,
                        safety_qty: d.safety_qty || 0,
                        in_transit_qty: d.in_transit_qty || 0,
                        warehouse_type: d.warehouse_type || p.warehouse_type,
                      }))
                      setTestSampleSku(d.sku)
                    }
                  } catch {}
                }}
                className="clickable"
                style={{
                  fontSize: 'var(--font-xs)',
                  padding: '4px 10px',
                  minHeight: 28,
                  borderRadius: 'var(--radius-full)',
                  border: '1px solid var(--border)',
                  background: 'var(--card)',
                  color: 'var(--primary)',
                  cursor: 'pointer',
                  fontWeight: 600,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                <IconLightning size={12} /> 用真实库存填充
              </button>
              {testSampleSku && (
                <span style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>
                  样本: {testSampleSku}
                </span>
              )}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                可用量
                <input
                  type="number"
                  value={testInv.available_qty}
                  onChange={e => setTestInv(p => ({ ...p, available_qty: e.target.value }))}
                  style={IS}
                />
              </label>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                安全线
                <span className="small muted" style={{ marginLeft: 6, fontSize: 'var(--font-xs)' }}>
                  运行时动态注入=日销×补货周期（模拟测试用下方数值）
                </span>
                <input
                  type="number"
                  value={testInv.safety_qty}
                  onChange={e => setTestInv(p => ({ ...p, safety_qty: e.target.value }))}
                  style={IS}
                />
              </label>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                在途
                <input
                  type="number"
                  value={testInv.in_transit_qty}
                  onChange={e => setTestInv(p => ({ ...p, in_transit_qty: e.target.value }))}
                  style={IS}
                />
              </label>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                滞销天数
                <input
                  type="number"
                  value={testInv.days_since_last}
                  onChange={e => setTestInv(p => ({ ...p, days_since_last: e.target.value }))}
                  style={IS}
                />
              </label>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                订单数量
                <input
                  type="number"
                  value={testInv.order_quantity}
                  onChange={e => setTestInv(p => ({ ...p, order_quantity: e.target.value }))}
                  style={IS}
                />
              </label>
              <label style={{ fontSize: 'var(--font-sm)' }}>
                仓库主体
                <select
                  value={testInv.warehouse_type}
                  onChange={e => setTestInv(p => ({ ...p, warehouse_type: e.target.value }))}
                  style={IS}
                >
                  <option value="">全部</option>
                  <option value="platform">C仓</option>
                  {globalChannel === 'jd' && <option value="platform_b">B仓</option>}
                  <option value="own">自有仓</option>
                </select>
              </label>
            </div>
            {(() => {
              const _cj = (rule && rule.condition_json) || ''
              const _needAdj = _cj.includes('adj_dos')
              const _needBuf = _cj.includes('buffer')
              const _needHealth = _cj.includes('health.')
              // 2026-11-01 自有仓/效期变量组可调 —— 原测试弹窗对 own.*/batch.* 无任何输入项,
              //   后端也无默认值 → 条件恒 None → 自有仓三级规则在测试里永远"未触发",
              //   用户会误判"规则配对没有"。现在按需出现输入框并透传 body.own / body.batch。
              const _needOwn = _cj.includes('own.')
              const _needBatch = _cj.includes('batch.')
              const _needParams =
                _cj.includes('params.') && testParams && Object.keys(testParams).length > 0
              if (
                !_needAdj &&
                !_needBuf &&
                !_needHealth &&
                !_needOwn &&
                !_needBatch &&
                !_needParams
              )
                return null
              return (
                <div
                  style={{
                    marginTop: 12,
                    padding: '10px 12px',
                    background: 'var(--bg)',
                    borderRadius: 'var(--radius-card)',
                    border: '1px solid var(--border)',
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 'var(--font-sm)', marginBottom: 8 }}>
                    <IconGear size={14} style={{ verticalAlign: -2 }} />{' '}
                    计算变量/规则参数（与看板判定同源，可调预览）
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    {_needAdj && (
                      <label style={{ fontSize: 'var(--font-sm)' }}>
                        可售天数 Adj-DOS
                        <input
                          type="number"
                          step="any"
                          value={testInv.adj_dos}
                          onChange={e => setTestInv(p => ({ ...p, adj_dos: e.target.value }))}
                          style={IS}
                          placeholder="例 2.5"
                        />
                      </label>
                    )}
                    {_needBuf && (
                      <label style={{ fontSize: 'var(--font-sm)' }}>
                        缓冲比 Buffer
                        <input
                          type="number"
                          step="any"
                          value={testInv.buffer}
                          onChange={e => setTestInv(p => ({ ...p, buffer: e.target.value }))}
                          style={IS}
                          placeholder="例 0.8"
                        />
                      </label>
                    )}
                    {_needHealth && (
                      <label style={{ fontSize: 'var(--font-sm)' }}>
                        库存健康分
                        <input
                          type="number"
                          step="any"
                          value={testInv.health_score}
                          onChange={e => setTestInv(p => ({ ...p, health_score: e.target.value }))}
                          style={IS}
                          placeholder="例 60"
                        />
                      </label>
                    )}
                    {_needOwn && (
                      <>
                        <label style={{ fontSize: 'var(--font-sm)' }}>
                          下游日销(件/天)
                          <input
                            type="number"
                            step="any"
                            value={testOwn.downstream_ds}
                            onChange={e =>
                              setTestOwn(p => ({ ...p, downstream_ds: e.target.value }))
                            }
                            style={IS}
                            placeholder="例 10"
                          />
                        </label>
                        <label style={{ fontSize: 'var(--font-sm)' }}>
                          自有可发(在仓+在途+采购在途)
                          <input
                            type="number"
                            step="any"
                            value={testOwn.can_ship}
                            onChange={e => setTestOwn(p => ({ ...p, can_ship: e.target.value }))}
                            style={IS}
                            placeholder="例 40"
                          />
                        </label>
                        <label style={{ fontSize: 'var(--font-sm)' }}>
                          调拨周期(天)
                          <input
                            type="number"
                            step="any"
                            value={testOwn.b2c_days}
                            onChange={e => setTestOwn(p => ({ ...p, b2c_days: e.target.value }))}
                            style={IS}
                            placeholder="例 6"
                          />
                        </label>
                        <label style={{ fontSize: 'var(--font-sm)' }}>
                          自有可撑天数(留空=按可用/日销推)
                          <input
                            type="number"
                            step="any"
                            value={testOwn.cover_days}
                            onChange={e => setTestOwn(p => ({ ...p, cover_days: e.target.value }))}
                            style={IS}
                            placeholder="例 120"
                          />
                        </label>
                      </>
                    )}
                    {_needBatch && (
                      <label style={{ fontSize: 'var(--font-sm)' }}>
                        距过期天数(负=已过期)
                        <input
                          type="number"
                          step="any"
                          value={testBatch.days_to_expire}
                          onChange={e =>
                            setTestBatch(p => ({ ...p, days_to_expire: e.target.value }))
                          }
                          style={IS}
                          placeholder="例 10"
                        />
                      </label>
                    )}
                    {_needParams &&
                      Object.keys(testParams).map(k => (
                        <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                          参数 {k}
                          <input
                            type="number"
                            step="any"
                            value={testParams[k]}
                            onChange={e => setTestParams(p => ({ ...p, [k]: e.target.value }))}
                            style={IS}
                          />
                        </label>
                      ))}
                  </div>
                </div>
              )
            })()}
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <button
                onClick={runTest}
                disabled={testLoading}
                className="btn btn-primary"
                style={{
                  flex: 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  justifyContent: 'center',
                  minHeight: 42,
                }}
              >
                {testLoading ? (
                  <>
                    <IconLoading size={14} /> 测试中...
                  </>
                ) : (
                  '运行测试'
                )}
              </button>
              <button
                onClick={onClose}
                className="btn btn-ghost"
                style={{ flex: 1, minHeight: 42 }}
              >
                关闭
              </button>
            </div>
            {testResult && (
              <div
                style={{
                  marginTop: 14,
                  padding: '12px 14px',
                  borderRadius: 'var(--radius-card)',
                  background: testResult.triggered
                    ? 'rgba(34,197,94,0.12)'
                    : 'rgba(239,68,68,0.08)',
                  border: '1px solid',
                  borderColor: testResult.triggered ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.3)',
                }}
              >
                <div
                  style={{
                    fontWeight: 700,
                    fontSize: 'var(--font-15)',
                    color: testResult.triggered ? 'var(--success)' : 'var(--danger)',
                    marginBottom: 6,
                  }}
                >
                  {testResult.triggered ? (
                    <>
                      <IconCheck size={15} style={{ verticalAlign: -3 }} /> 触发告警
                    </>
                  ) : (
                    <>
                      <IconClose size={15} style={{ verticalAlign: -3 }} /> 未触发
                    </>
                  )}
                </div>
                {testResult.triggered && (
                  <div style={{ fontSize: 'var(--font-sm)', color: 'var(--text)' }}>
                    <div>
                      <b>{testResult.alert_title}</b>
                    </div>
                    <div className="small muted">{testResult.alert_desc}</div>
                  </div>
                )}
                {testResult.detail && (
                  <div
                    style={{
                      fontSize: 'var(--font-xs)',
                      color: 'var(--muted2)',
                      marginTop: 8,
                      borderTop: '1px dashed var(--border)',
                      paddingTop: 8,
                    }}
                  >
                    条件: 当{' '}
                    <b>
                      {testResult.detail.left === 'inv.warehouse_type'
                        ? // 字段级仓储条件(自有仓规则的固定前置)不再叠仓前缀
                          ''
                        : testResult.detail.warehouse
                          ? testResult.detail.warehouse === 'platform_b'
                            ? 'B仓'
                            : testResult.detail.warehouse === 'platform'
                              ? 'C仓'
                              : '自有仓'
                          : '全部'}
                    </b>{' '}
                    {fieldLbl(testResult.detail.left)} {opLbl(testResult.detail.op)}{' '}
                    {String(testResult.detail.right).includes('.')
                      ? fieldLbl(testResult.detail.right)
                      : testResult.detail.right}
                    <br />
                    计算: 左侧值 = {String(testResult.detail.left_value)}
                    {String(testResult.detail.right_value).startsWith('max(') ? (
                      <>，右侧 = {testResult.detail.right_value}</>
                    ) : (
                      <>，右侧值 = {String(testResult.detail.right_value)}</>
                    )}
                    {/* 2026-11-01 嵌套子条件逐条回显(仅 and/or 规则有) —— 顶层那对比值命中
                                不代表整条命中, 用户需看到被哪个子条件拦住 */}
                    {testResult.detail.and && (
                      <div style={{ marginTop: 6 }}>
                        <b style={{ color: 'var(--muted)' }}>且</b>{' '}
                        {fieldLbl(testResult.detail.and.left)} {opLbl(testResult.detail.and.op)}{' '}
                        {String(testResult.detail.and.right).includes('.')
                          ? fieldLbl(testResult.detail.and.right)
                          : testResult.detail.and.right}{' '}
                        <span
                          style={{
                            color: testResult.detail.and.hit ? 'var(--success)' : 'var(--danger)',
                            fontWeight: 600,
                          }}
                        >
                          {testResult.detail.and.hit ? '命中' : '未命中'}
                        </span>
                        <span className="muted">
                          {' '}
                          ({String(testResult.detail.and.left_value)} vs{' '}
                          {String(testResult.detail.and.right_value)})
                        </span>
                      </div>
                    )}
                    {Array.isArray(testResult.detail.or) && testResult.detail.or.length > 0 && (
                      <div style={{ marginTop: 6 }}>
                        <b style={{ color: 'var(--muted)' }}>或(任一)</b>
                        {testResult.detail.or.map((o, oi) => (
                          <div key={oi} style={{ paddingLeft: 8 }}>
                            · {fieldLbl(o.left)} {opLbl(o.op)}{' '}
                            {String(o.right).includes('.') ? fieldLbl(o.right) : o.right}{' '}
                            <span
                              style={{
                                color: o.hit ? 'var(--success)' : 'var(--danger)',
                                fontWeight: 600,
                              }}
                            >
                              {o.hit ? '命中' : '未命中'}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
