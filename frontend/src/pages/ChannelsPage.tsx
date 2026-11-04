import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import { Group, Row, LastRow } from '../components/ListGroup'
import { useAppStore, type Channel } from '../store/useAppStore'
import { channelName } from '../components/channels'
import { t } from '../locale'

/**
 * 渠道管理(2026-10-30 P2 界面)
 *
 * 让用户自己增删改渠道 —— 无需改代码发版:
 *   code          渠道标识(小写, 存库用, 创建后不可改)
 *   name          显示名(下拉/标题用)
 *   supports_b    是否走 BBCC 体系(B 仓调拨链) —— 决定补货/采购是否含 B 仓
 *   default_mode  默认补货模式(bbcc / traditional)
 *   copy_from     新建时复制哪个渠道的规则与参数(可选)
 * 禁用而非删除: 存量 inventory/orders/rules 仍带该 channel, 物理删会产生孤儿数据。
 */
export default function ChannelsPage() {
  const toast = useToast()
  const setChannels = useAppStore(s => s.setChannels)
  const [list, setList] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    code: '',
    name: '',
    supports_b: false,
    default_mode: 'traditional',
    copy_from: '',
  })
  const [showForm, setShowForm] = useState(false)

  const load = () => {
    setLoading(true)
    api
      .get('/api/channels', { timeout: 20000 })
      .then(r => {
        // 2026-10-30 注意: apiGet 已解包(`return { data: 'data' in d ? d.data : d }`),
        //   故 r.data 直接就是数组; 原写 r.data.data 恒 undefined → 列表永远空(用户实证无数据)
        const d = Array.isArray(r.data) ? r.data : []
        setList(d)
        setChannels(d)
      })
      // 2026-11-01 修复: useToast() 返回对象({success/error/warning/info}), 不是函数——
      //   原 `toast && toast(msg)` 运行时 TypeError(对象不可调用), 且抛错被 .catch 二次吞掉
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.list_failed')))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const create = () => {
    const code = form.code.trim().toLowerCase()
    if (!/^[a-z][a-z0-9_]{0,19}$/.test(code)) {
      toast.warning(t('ch.err_code'))
      return
    }
    setSaving(true)
    api
      .post(
        '/api/channels',
        {
          code,
          name: form.name.trim() || code,
          supports_b: form.supports_b,
          default_mode: form.default_mode,
          copy_from: form.copy_from || undefined,
        },
        { timeout: 60000 },
      )
      .then(r => {
        const cp = (r.data && r.data.copied) || {}
        toast.success(
          cp.rules != null
            ? t('ch.created_copied', {
                name: form.name || code,
                rules: cp.rules,
                cfg: cp.config,
              })
            : t('ch.created', { name: form.name || code }),
        )
        setForm({
          code: '',
          name: '',
          supports_b: false,
          default_mode: 'traditional',
          copy_from: '',
        })
        setShowForm(false)
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.create_failed')))
      .finally(() => setSaving(false))
  }

  const patch = (code: string, body: Record<string, unknown>) => {
    api
      .put('/api/channels/' + encodeURIComponent(code), body, { timeout: 20000 })
      .then(() => {
        toast.success(t('ch.saved'))
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.save_failed')))
  }

  const disable = (code: string) => {
    if (!window.confirm(t('ch.disable_confirm', { code }))) return
    api
      .delete('/api/channels/' + encodeURIComponent(code), { timeout: 20000 })
      .then(() => {
        toast.success(t('ch.disabled'))
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.disable_failed')))
  }

  return (
    <div className="form-page">
      <Group title={t('set.channels')}>
        {loading ? (
          // 2026-11-02 S2-9: 首载走骨架(两行占位), 不再是单行"加载中..." —— 骨架不让卡片塌陷跳版
          <div style={{ padding: '14px 16px' }}>
            <div className="skeleton" style={{ width: '38%', height: 16, marginBottom: 8 }} />
            <div className="skeleton" style={{ width: '62%', height: 12, marginBottom: 0 }} />
          </div>
        ) : list.length === 0 ? (
          <Row label={t('ch.empty')} sub={t('ch.empty_sub')} />
        ) : (
          list.map(c => (
            <Row
              key={c.code}
              label={`${channelName(c)}${c.enabled === 0 ? t('ch.suffix_disabled') : ''}`}
              sub={`${c.code} · ${
                c.default_mode === 'bbcc' ? 'BBCC' : t('ch.mode_traditional')
              }${c.supports_b ? t('ch.with_b') : ''}`}
            />
          ))
        )}
        <LastRow
          label={showForm ? t('ch.collapse') : t('ch.add')}
          sub={t('ch.add_sub')}
          onClick={() => setShowForm(v => !v)}
        />
      </Group>

      {showForm && (
        <Group title={t('ch.add')}>
          <div className="form-grid">
            <label className="form-field">
              {t('ch.code_label')}
              <input
                value={form.code}
                onChange={e => setForm({ ...form, code: e.target.value })}
                placeholder={t('ch.code_ph')}
                className="form-input"
              />
            </label>
            <label className="form-field">
              {t('ch.name_label')}
              <input
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder={t('ch.name_ph')}
                className="form-input"
              />
            </label>
            <label className="form-field">
              {t('ch.mode_label')}
              <select
                value={form.default_mode}
                onChange={e => setForm({ ...form, default_mode: e.target.value })}
                className="form-input"
              >
                <option value="traditional">{t('ch.mode_traditional_full')}</option>
                <option value="bbcc">{t('ch.mode_bbcc')}</option>
              </select>
            </label>
            <label className="form-check">
              <input
                type="checkbox"
                checked={form.supports_b}
                onChange={e => setForm({ ...form, supports_b: e.target.checked })}
              />
              {t('ch.supports_b')}
            </label>
            <label className="form-field">
              {t('ch.copy_from')}
              <select
                value={form.copy_from}
                onChange={e => setForm({ ...form, copy_from: e.target.value })}
                className="form-input"
              >
                <option value="">{t('ch.copy_none')}</option>
                {list.map(c => (
                  <option key={c.code} value={c.code}>
                    {c.name}（{c.code}）
                  </option>
                ))}
              </select>
            </label>
            <button className="btn btn-primary" disabled={saving} onClick={create}>
              {saving ? t('ch.creating') : t('ch.create')}
            </button>
          </div>
        </Group>
      )}

      {list.length > 0 &&
        (() => {
          // 2026-11-02 S2-9: 卡片整体末行不画分割线(last) —— 否则贴卡片圆角底边出现一道
          //   "多余横线"。两组(可禁用 / 可重新启用)各自知道自己是不是全卡片最后一行。
          const actives = list.filter(c => c.enabled !== 0)
          const disabled = list.filter(c => c.enabled === 0)
          return (
            <Group title={t('ch.group_disable')}>
              {actives.map((c, i) => (
                <Row
                  key={'d' + c.code}
                  label={t('ch.disable_row', { name: channelName(c) })}
                  danger
                  loading={saving}
                  last={disabled.length === 0 && i === actives.length - 1}
                  onClick={() => disable(c.code)}
                />
              ))}
              {disabled.map((c, i) => (
                <Row
                  key={'e' + c.code}
                  label={t('ch.enable_row', { name: channelName(c) })}
                  sub={c.code}
                  loading={saving}
                  last={i === disabled.length - 1}
                  onClick={() => patch(c.code, { enabled: 1 })}
                />
              ))}
            </Group>
          )
        })()}
    </div>
  )
}
