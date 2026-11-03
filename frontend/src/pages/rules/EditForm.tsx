import {
  IconAlert,
  IconClipboard,
  IconClose,
  IconGear,
  IconLightning,
  IconLoading,
  IconSave,
  IconScale,
} from '../../components/Icons'
import { t } from '../../locale'
import { EVENTS, LF, MODES, OPS, WHS, fieldLbl, opLbl, renderTmpl } from './labels'
import { IS } from './shared'

/**
 * 规则编辑表单(全受控) —— 自 RulesPage.tsx 外提(S3-1 续2)。
 * 状态一行不搬: f/cond/rParams/editing 及其 setter 与 save/cancelEdit 全部留在页面,
 * 以同名 props 注入; 「测试」按钮通过 onTest 闭包回调页面(打包 setTestRule)。
 * TYPE_TEMPLATE/defaultParams 仅本表单使用, 随迁。
 */
export default function RuleEditForm({
  f,
  cond,
  rParams,
  editing,
  saveLoading,
  setF,
  setCond,
  setRParams,
  cancelEdit,
  save,
  onTest,
  globalChannel,
}: {
  f: any
  cond: any
  rParams: Record<string, string>
  editing: any
  saveLoading: boolean
  setF: (v: any) => void
  setCond: (v: any) => void
  setRParams: (v: Record<string, string>) => void
  cancelEdit: () => void
  save: () => void
  onTest: () => void
  globalChannel: string
}) {
  const defaultParams = at =>
    at === 'stockout'
      ? {
          lit: '3',
          otif_min: '0.6',
          ss_z: '1.65',
          sigma_days: '60',
          accel_ratio: '1.3',
          accel_min_qty: '10',
          buffer_orange: '1.2',
          buffer_yellow: '1.0',
          orange_slack_days: '1',
          include_avail_zero: '1',
          log: '0',
          alert_enabled: '1',
        }
      : at === 'health'
        ? { health_good: '85', health_warning: '60', log: '0', alert_enabled: '1' }
        : at === 'own_transfer_gap'
          ? { transfer_gap_min: '1', transfer_cover_mult: '1.0', log: '0', alert_enabled: '1' }
          : at === 'own_overstock'
            ? { own_overstock_days: '90', log: '0', alert_enabled: '1' }
            : at === 'own_expiry_risk'
              ? { own_expiry_buffer_days: '15', log: '0', alert_enabled: '1' }
              : { alert_enabled: '1' }
  const TYPE_TEMPLATE = {
    low_stock: {
      event: 'inventory.changed',
      cond: {
        left: 'inv.available_qty',
        op: '<',
        right: 'inv.safety_qty',
        rightType: 'field',
        pctValue: 100,
        warehouse: '',
      },
    },
    stockout: {
      event: 'scheduled.daily',
      cond: {
        left: 'inv.adj_dos',
        op: '<=',
        right: 'params.lit',
        rightType: 'field',
        pctValue: 100,
        warehouse: '',
      },
    },
    health: {
      event: 'scheduled.daily',
      cond: {
        left: 'health.score',
        op: '<',
        right: 'params.health_warning',
        rightType: 'field',
        pctValue: 100,
        warehouse: '',
      },
    },
    oversell: {
      event: 'order.created',
      cond: {
        // 2026-10-29 累计口径: 单笔 quantity 会漏报"累计超卖"(历史订单每笔都小于库存)
        left: 'order.total_qty',
        op: '>',
        right: 'inv.available_qty',
        rightType: 'field',
        pctValue: 100,
        warehouse: '',
      },
    },
    slow_moving: {
      event: 'scheduled.daily',
      cond: {
        left: 'days_since_last',
        op: '>',
        right: '30',
        rightType: 'number',
        pctValue: 100,
        warehouse: '',
      },
    },
    // 2026-11-01 自有仓供给侧三条(与看板「自有仓健康」卡同源; 判据也写在 seed_fill 内置规则里)
    // 都带 inv.warehouse_type=='own' 前置 —— own.* 键只在 own 行注入, 缺前置则规则永不触发
    own_transfer_gap: {
      event: 'inventory.changed',
      cond: {
        left: 'inv.warehouse_type',
        op: '==',
        right: 'own',
        rightType: 'value',
        pctValue: 100,
        warehouse: '',
      },
    },
    own_overstock: {
      event: 'scheduled.daily',
      cond: {
        left: 'inv.warehouse_type',
        op: '==',
        right: 'own',
        rightType: 'value',
        pctValue: 100,
        warehouse: '',
      },
    },
    own_expiry_risk: {
      event: 'scheduled.daily',
      cond: {
        left: 'inv.warehouse_type',
        op: '==',
        right: 'own',
        rightType: 'value',
        pctValue: 100,
        warehouse: '',
      },
    },
  }

  return (
    <div
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)',
        padding: 16,
        marginBottom: 16,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 12 }}>
        {editing.id ? t('rules.edit') : t('rules.new_btn') + '规则'}
      </div>

      {/* 名称 + {t("rules.severity")} + 补货模式 */}
      <div
        style={{
          display: 'flex',
          gap: 12,
          alignItems: 'flex-end',
          marginBottom: 14,
          flexWrap: 'wrap',
        }}
      >
        <label style={{ flex: 1, minWidth: 140, fontSize: 'var(--font-sm)' }}>
          {t('rules.name')}
          <input
            value={f.name}
            onChange={e => setF({ ...f, name: e.target.value })}
            style={IS}
            placeholder="例：低库存预警"
          />
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          级别
          <div className="hammer-segmented" style={{ marginTop: 4, flexShrink: 0 }}>
            {[
              { v: 'warning', t: '警告', c: 'var(--warning)' },
              { v: 'error', t: t('rules.severity_error'), c: 'var(--danger)' },
              { v: 'info', t: '提示', c: 'var(--primary)' },
            ].map(({ v, t, c }) => (
              <span
                key={v}
                onClick={() => setF({ ...f, severity: v })}
                className={'hammer-segment' + (f.severity === v ? ' active' : '')}
                style={{
                  minWidth: 46,
                  color: f.severity === v ? c : undefined,
                  fontWeight: f.severity === v ? 700 : undefined,
                }}
              >
                {t}
              </span>
            ))}
          </div>
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          补货模式
          <select
            value={f.mode || ''}
            onChange={e => setF({ ...f, mode: e.target.value })}
            style={{
              ...IS,
              fontSize: 'var(--font-13)',
              marginTop: 4,
              width: '100%',
              minWidth: 80,
            }}
          >
            {MODES.filter(m => m.v !== 'bbcc' || globalChannel === 'jd').map(m => (
              <option key={m.v} value={m.v}>
                {m.l}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          规则类型
          <select
            value={f.alert_type || 'low_stock'}
            onChange={e => {
              const at = e.target.value
              setF({
                ...f,
                alert_type: at,
                event: (TYPE_TEMPLATE[at] || {}).event || f.event,
              })
              const tm = (TYPE_TEMPLATE[at] || {}).cond
              if (tm) setCond({ ...tm })
              setRParams(defaultParams(at))
            }}
            style={{
              ...IS,
              fontSize: 'var(--font-13)',
              marginTop: 4,
              width: '100%',
              minWidth: 110,
            }}
          >
            <option value="low_stock">低库存(动态安全线)</option>
            <option value="stockout">濒临断货(看板同源)</option>
            <option value="health">健康监控(看板同源)</option>
            <option value="oversell">超卖保护</option>
            <option value="slow_moving">滞销识别</option>
            {/* 2026-11-01 自有仓供给侧(看板「自有仓健康」卡同源) ——
                          自有仓不产生销售, 低库存口径不适用, 故单列这组 */}
            <option value="own_transfer_gap">自有仓·调拨覆盖不足</option>
            <option value="own_overstock">自有仓·积压</option>
            <option value="own_expiry_risk">自有仓·效期风险</option>
            <option value="custom">自定义</option>
          </select>
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          触发事件
          <select
            value={f.event || 'inventory.changed'}
            onChange={e => setF({ ...f, event: e.target.value })}
            style={{
              ...IS,
              fontSize: 'var(--font-13)',
              marginTop: 4,
              width: '100%',
              minWidth: 110,
            }}
          >
            {EVENTS.map(ev => (
              <option key={ev.value} value={ev.value}>
                {ev.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 触发条件 — 一句话 */}
      <div
        style={{
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          padding: 14,
          marginBottom: 14,
        }}
      >
        <div
          style={{
            fontWeight: 600,
            fontSize: 'var(--font-13)',
            marginBottom: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <IconScale size={14} /> 触发条件
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="text-14 font-500">当</span>
          <select
            value={cond.warehouse}
            onChange={e => setCond(p => ({ ...p, warehouse: e.target.value }))}
            style={{ ...IS, flex: 1, minWidth: 60, fontSize: 'var(--font-13)' }}
          >
            {WHS.filter(w => w.v !== 'platform_b' || globalChannel === 'jd').map(w => (
              <option key={w.v} value={w.v}>
                {w.l}
              </option>
            ))}
          </select>
          <select
            value={cond.left}
            onChange={e => setCond(p => ({ ...p, left: e.target.value }))}
            style={{ ...IS, flex: 2, minWidth: 120, fontSize: 'var(--font-md)' }}
          >
            {LF.map(f => (
              <option key={f.v} value={f.v}>
                {f.l}
              </option>
            ))}
          </select>
          <select
            value={cond.op}
            onChange={e => setCond(p => ({ ...p, op: e.target.value }))}
            style={{ ...IS, width: 70, fontSize: 'var(--font-md)', textAlign: 'center' }}
          >
            {OPS.map(o => (
              <option key={o.v} value={o.v}>
                {o.l}
              </option>
            ))}
          </select>
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              flex: 2,
              minWidth: 140,
            }}
          >
            <input
              type="number"
              value={cond.pctValue || 0}
              onChange={e =>
                setCond(p => ({
                  ...p,
                  pctValue: parseInt(e.target.value) || 0,
                  rightType: 'pct',
                  right: 'inv.safety_qty',
                }))
              }
              min={1}
              max={200}
              style={{
                ...IS,
                width: 'auto',
                flex: 1,
                fontSize: 'var(--font-md)',
                textAlign: 'center',
              }}
            />
            <span
              style={{
                fontSize: 'var(--font-13)',
                color: 'var(--muted2)',
                fontWeight: 500,
                whiteSpace: 'nowrap',
              }}
            >
              {cond.left === 'days_since_last'
                ? '天'
                : cond.left === 'inv.available_qty'
                  ? '%（安全库存百分比）'
                  : cond.left === 'order.quantity'
                    ? '件'
                    : cond.left === 'order.total_amount'
                      ? '元'
                      : '%'}
            </span>
          </span>
        </div>
        <div
          className="small"
          style={{
            marginTop: 8,
            padding: '6px 10px',
            background: 'var(--bg)',
            borderRadius: 'var(--radius-lg)',
            fontSize: 'var(--font-13)',
            color: 'var(--primary)',
          }}
        >
          <IconClipboard
            size={12}
            style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }}
          />
          当 <b>{WHS.find(w => w.v === cond.warehouse)?.l || '全部'}</b>{' '}
          <b>{fieldLbl(cond.left)}</b> {opLbl(cond.op)}{' '}
          <b>
            {cond.pctValue || 0}
            {cond.left === 'days_since_last'
              ? '天'
              : cond.left === 'inv.available_qty'
                ? '%'
                : '件'}
          </b>
          {cond.left === 'inv.available_qty' ? (
            <span style={{ color: 'var(--muted2)', fontSize: 'var(--font-xs)' }}>
              （安全库存的 {cond.pctValue || 0}%）
            </span>
          ) : (
            ''
          )}
          时
          {(cond.or || []).map((o, i) => (
            <span key={i} style={{ color: 'var(--primary)' }}>
              {' '}
              <b>或</b> {WHS.find(w => w.v === o.warehouse)?.l || '全部'} <b>{fieldLbl(o.left)}</b>{' '}
              {opLbl(o.op)} <b>{o.right}</b>
            </span>
          ))}
        </div>
        {/* P2 或条件组(可拓展: 多条件任一满足即触发, 如断货'时间线或缓冲破位') */}
        {(cond.or || []).map((o, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              gap: 6,
              alignItems: 'center',
              flexWrap: 'wrap',
              marginTop: 8,
              padding: '8px 10px',
              background: 'var(--bg)',
              borderRadius: 'var(--radius-lg)',
              border: '1px dashed var(--border)',
            }}
          >
            <span className="text-13 font-600" style={{ color: 'var(--primary)' }}>
              或
            </span>
            <select
              value={o.warehouse}
              onChange={e => {
                const or = [...(cond.or || [])]
                or[i] = { ...or[i], warehouse: e.target.value }
                setCond(p => ({ ...p, or }))
              }}
              style={{ ...IS, flex: 1, minWidth: 60, fontSize: 'var(--font-sm)' }}
            >
              {WHS.filter(w => w.v !== 'platform_b' || globalChannel === 'jd').map(w => (
                <option key={w.v} value={w.v}>
                  {w.l}
                </option>
              ))}
            </select>
            <select
              value={o.left}
              onChange={e => {
                const or = [...(cond.or || [])]
                or[i] = { ...or[i], left: e.target.value }
                setCond(p => ({ ...p, or }))
              }}
              style={{ ...IS, flex: 2, minWidth: 110, fontSize: 'var(--font-sm)' }}
            >
              {LF.map(f => (
                <option key={f.v} value={f.v}>
                  {f.l}
                </option>
              ))}
            </select>
            <select
              value={o.op}
              onChange={e => {
                const or = [...(cond.or || [])]
                or[i] = { ...or[i], op: e.target.value }
                setCond(p => ({ ...p, or }))
              }}
              style={{
                ...IS,
                width: 64,
                fontSize: 'var(--font-sm)',
                textAlign: 'center',
              }}
            >
              {OPS.map(x => (
                <option key={x.v} value={x.v}>
                  {x.l}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="any"
              value={o.right === 'inv.safety_qty' ? '' : o.right}
              placeholder={o.right === 'inv.safety_qty' ? '动态安全线' : ''}
              onChange={e => {
                const or = [...(cond.or || [])]
                or[i] = {
                  ...or[i],
                  right: e.target.value || 'inv.safety_qty',
                  rightType: 'number',
                }
                setCond(p => ({ ...p, or }))
              }}
              style={{ ...IS, flex: 1, minWidth: 80, fontSize: 'var(--font-sm)' }}
            />
            <span
              onClick={() => setCond(p => ({ ...p, or: (p.or || []).filter((_, j) => j !== i) }))}
              className="clickable"
              style={{
                fontSize: 'var(--font-sm)',
                color: 'var(--danger)',
                cursor: 'pointer',
                padding: '9px 12px',
                minHeight: 32,
              }}
            >
              <IconClose size={16} />
            </span>
          </div>
        ))}
        <button
          onClick={() =>
            setCond(p => ({
              ...p,
              or: [
                ...(p.or || []),
                {
                  left: 'inv.buffer',
                  op: '<=',
                  right: '1',
                  rightType: 'number',
                  warehouse: '',
                  pctValue: 100,
                },
              ],
            }))
          }
          className="clickable"
          style={{
            marginTop: 8,
            fontSize: 'var(--font-sm)',
            padding: '4px 12px',
            borderRadius: 'var(--radius-full)',
            border: '1px dashed var(--primary)',
            background: 'transparent',
            color: 'var(--primary)',
            cursor: 'pointer',
          }}
        >
          ＋ 或条件（任一满足触发）
        </button>
      </div>

      {/* 告警内容 */}
      <div
        style={{
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          padding: 14,
        }}
      >
        <div
          style={{
            fontWeight: 600,
            fontSize: 'var(--font-13)',
            marginBottom: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <IconAlert size={14} /> 告警内容
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span className="muted2" style={{ fontSize: 'var(--font-10)' }}>
              生成告警
            </span>
            <span className="hammer-segmented" style={{ flexShrink: 0 }}>
              <span
                className={'hammer-segment' + (rParams.alert_enabled !== '0' ? ' active' : '')}
                onClick={() => setRParams({ ...rParams, alert_enabled: '1' })}
                style={{ minWidth: 34, padding: '2px 6px' }}
              >
                开
              </span>
              <span
                className={'hammer-segment' + (rParams.alert_enabled === '0' ? ' active' : '')}
                onClick={() => setRParams({ ...rParams, alert_enabled: '0' })}
                style={{ minWidth: 34, padding: '2px 6px' }}
              >
                关
              </span>
            </span>
          </span>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
          <label style={{ flex: 1, minWidth: 180, fontSize: 'var(--font-sm)' }}>
            告警标题
            <div
              style={{
                marginTop: 4,
                padding: '8px 12px',
                background: 'var(--bg)',
                borderRadius: 'var(--radius-lg)',
                fontSize: 'var(--font-md)',
                minHeight: 36,
                border: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 3,
              }}
            >
              {renderTmpl(f.alert_title) || (
                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  输入文字或点击下方按钮插入变量
                </span>
              )}
            </div>
            <input
              value={f.alert_title}
              onChange={e => setF({ ...f, alert_title: e.target.value })}
              style={{ ...IS, fontSize: 'var(--font-13)', marginTop: 4 }}
              placeholder="输入文字，点击下方按钮插入变量"
            />
            <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
              {[
                { v: '{product_name}', l: '商品名' },
                { v: '{sku}', l: 'SKU' },
              ].map(t => (
                <span
                  key={t.v}
                  onClick={() => setF({ ...f, alert_title: f.alert_title + t.v })}
                  className="clickable"
                  style={{
                    padding: '4px 12px',
                    borderRadius: 'var(--radius-full)',
                    fontSize: 'var(--font-sm)',
                    background: 'rgba(29,78,216,0.1)',
                    color: 'var(--primary)',
                    cursor: 'pointer',
                    border: '1px solid rgba(29,78,216,0.2)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 3,
                  }}
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                  {t.l}
                </span>
              ))}
            </div>
          </label>
          <label style={{ flex: 1, minWidth: 180, fontSize: 'var(--font-sm)' }}>
            告警描述
            <div
              style={{
                marginTop: 4,
                padding: '8px 12px',
                background: 'var(--bg)',
                borderRadius: 'var(--radius-lg)',
                fontSize: 'var(--font-md)',
                minHeight: 36,
                border: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 3,
              }}
            >
              {renderTmpl(f.alert_desc) || (
                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  输入文字或点击下方按钮插入变量
                </span>
              )}
            </div>
            <input
              value={f.alert_desc}
              onChange={e => setF({ ...f, alert_desc: e.target.value })}
              style={{ ...IS, fontSize: 'var(--font-13)', marginTop: 4 }}
              placeholder="输入文字，点击下方按钮插入变量"
            />
            <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
              {[
                { v: '{avail}', l: '可用量' },
                { v: '{safety}', l: '动态安全线' },
                { v: '{sku}', l: 'SKU' },
              ].map(t => (
                <span
                  key={t.v}
                  onClick={() => setF({ ...f, alert_desc: f.alert_desc + t.v })}
                  className="clickable"
                  style={{
                    padding: '4px 12px',
                    borderRadius: 'var(--radius-full)',
                    fontSize: 'var(--font-sm)',
                    background: 'rgba(29,78,216,0.1)',
                    color: 'var(--primary)',
                    cursor: 'pointer',
                    border: '1px solid rgba(29,78,216,0.2)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 3,
                  }}
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                  {t.l}
                </span>
              ))}
            </div>
          </label>
        </div>
      </div>

      {/* 业务计算参数(融合: 断货/健康类规则携带看板计算参数, 保存进 rules.params; stock-risk/health_index 同源读取) */}
      {(f.alert_type === 'stockout' || f.alert_type === 'health') && (
        <div
          style={{
            background: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            padding: 14,
            marginTop: 14,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 'var(--font-13)',
              marginBottom: 4,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <IconGear size={13} style={{ verticalAlign: -2 }} />{' '}
            {f.alert_type === 'stockout'
              ? '断货计算参数(看板断货卡同源)'
              : '健康分档参数(看板健康卡同源)'}
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'stockout'
              ? '配置后看板断货判定(Adj-DOS/OTIF/动态SS/分级阈值)即时按此计算，停用/删除本规则则回默认值'
              : '配置后看板库存健康度分档(good/warning)即时按此计算，停用/删除本规则则回默认值'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'stockout'
              ? [
                  ['lit', '补货周期 L/T(天, 留空=按模式自动)'],
                  ['otif_min', 'OTIF 置信下限(0.6~1)'],
                  ['ss_z', '动态SS Z值(1.28/1.65/2.33)'],
                  ['sigma_days', '波动率σ样本天数(默认60, 与断货卡同源)'],
                  ['accel_ratio', '加速倍率阈值(默认1.3)'],
                  ['accel_min_qty', '加速样本量(默认10)'],
                  ['buffer_orange', '橙灯缓冲上限(默认1.2)'],
                  ['buffer_yellow', '黄灯缓冲上限(默认1.0)'],
                  ['orange_slack_days', '橙灯天数余量(默认1)'],
                  ['include_avail_zero', '已断纳入红灯 1/0'],
                  ['log', '审计日志 1/0(触发写 quality_logs)'],
                ]
              : [
                  ['health_good', '健康档位线(默认85)'],
                  ['health_warning', '预警档位线(默认60)'],
                  ['log', '审计日志 1/0(触发写 quality_logs)'],
                ]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {l}
                <input
                  type="number"
                  step="any"
                  value={rParams[k] ?? ''}
                  onChange={e => setRParams({ ...rParams, [k]: e.target.value })}
                  style={{
                    ...IS,
                    fontSize: 'var(--font-13)',
                    marginTop: 2,
                    padding: '6px 10px',
                  }}
                  placeholder="默认值"
                />
              </label>
            ))}
          </div>
        </div>
      )}

      {/* 2026-11-01 自有仓供给侧参数(看板「自有仓健康」卡同源) ——
                    原只写进 rules.params 的默认值, 表单里没有输入框 = 用户改不了阈值,
                    只能在"测试"弹窗里临时覆盖。三条规则各暴露自己的口径参数。 */}
      {(f.alert_type === 'own_transfer_gap' ||
        f.alert_type === 'own_overstock' ||
        f.alert_type === 'own_expiry_risk') && (
        <div
          style={{
            background: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            padding: 14,
            marginTop: 14,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 'var(--font-13)',
              marginBottom: 4,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <IconGear size={13} style={{ verticalAlign: -2 }} /> 自有仓口径参数(看板同源)
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'own_transfer_gap'
              ? '缺口 = 下游日销 × 调拨周期 − 自有可发(在仓+在途+采购在途)；小于阈值不报，避免零头噪音'
              : f.alert_type === 'own_overstock'
                ? '可撑天数 = 自有可用 ÷ 下游日销；超过阈值判积压(占用资金与库位)'
                : '距过期天数低于缓冲即报；另含"按当前消化速度赶不上效期"的加速预警'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'own_transfer_gap'
              ? [
                  ['transfer_gap_min', '缺口下限(件, 低于不报, 默认1)'],
                  ['b2c_days', '调拨周期(天, 留空=按补货模式取 B→C+缓冲)'],
                ]
              : f.alert_type === 'own_overstock'
                ? [['own_overstock_days', '积压天数阈值(默认90)']]
                : [['own_expiry_buffer_days', '效期缓冲(天, 默认15)']]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {l}
                <input
                  type="number"
                  step="any"
                  value={rParams[k] ?? ''}
                  onChange={e => setRParams({ ...rParams, [k]: e.target.value })}
                  style={{
                    ...IS,
                    fontSize: 'var(--font-13)',
                    marginTop: 2,
                    padding: '6px 10px',
                  }}
                  placeholder="默认值"
                />
              </label>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
        <button
          onClick={onTest}
          className="btn btn-ghost"
          style={{
            flex: 1,
            minHeight: 40,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            justifyContent: 'center',
          }}
        >
          <IconLightning size={14} /> 测试
        </button>
        <button
          onClick={save}
          disabled={saveLoading}
          className="btn btn-primary"
          style={{
            flex: 1,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            justifyContent: 'center',
            minHeight: 40,
          }}
        >
          {saveLoading ? (
            <>
              <IconLoading size={14} /> 保存中...
            </>
          ) : (
            <>
              <IconSave size={14} /> {t('common.save')}
            </>
          )}
        </button>
        <button
          onClick={cancelEdit}
          className="btn btn-ghost"
          style={{ flex: 1, background: 'var(--warning)', color: '#fff', minHeight: 40 }}
        >
          {t('common.cancel')}
        </button>
      </div>
    </div>
  )
}
