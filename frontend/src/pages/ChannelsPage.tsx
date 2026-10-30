import React, { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import { Group, Row, LastRow } from '../components/ListGroup'
import { useAppStore, type Channel } from '../store/useAppStore'

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
        const d = (r.data && r.data.data) || []
        setList(Array.isArray(d) ? d : [])
        setChannels(Array.isArray(d) ? d : [])
      })
      .catch(() => toast && toast('渠道列表加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const create = () => {
    const code = form.code.trim().toLowerCase()
    if (!/^[a-z][a-z0-9_]{0,19}$/.test(code)) {
      toast && toast('code 需小写字母开头，1-20 位字母/数字/下划线')
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
        if (r.data && r.data.ok === false) {
          toast && toast(r.data.error || '创建失败')
          return
        }
        const cp = (r.data && r.data.data && r.data.data.copied) || {}
        toast &&
          toast(
            cp.rules != null
              ? `已创建「${form.name || code}」，复制规则 ${cp.rules} 条、参数 ${cp.config} 项`
              : `已创建「${form.name || code}」`,
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
      .catch(() => toast && toast('创建失败'))
      .finally(() => setSaving(false))
  }

  const patch = (code: string, body: Record<string, unknown>) => {
    api
      .put('/api/channels/' + encodeURIComponent(code), body, { timeout: 20000 })
      .then(r => {
        if (r.data && r.data.ok === false) toast && toast(r.data.error || '保存失败')
        else {
          toast && toast('已保存')
          load()
        }
      })
      .catch(() => toast && toast('保存失败'))
  }

  const disable = (code: string) => {
    if (
      !window.confirm(`禁用渠道「${code}」？\n\n禁用后下拉不再出现，但已有数据保留（可再次启用）。`)
    )
      return
    api
      .delete('/api/channels/' + encodeURIComponent(code), { timeout: 20000 })
      .then(() => {
        toast && toast('已禁用')
        load()
      })
      .catch(() => toast && toast('禁用失败'))
  }

  return (
    <div style={{ padding: '16px 0', maxWidth: 560, margin: '0 auto' }}>
      <Group title="渠道管理">
        {loading ? (
          <Row label="加载中..." />
        ) : list.length === 0 ? (
          <Row label="暂无渠道" sub="点下方「新增渠道」创建" />
        ) : (
          list.map(c => (
            <Row
              key={c.code}
              label={`${c.name}${c.enabled === 0 ? '（已禁用）' : ''}`}
              sub={`${c.code} · ${c.default_mode === 'bbcc' ? 'BBCC' : '传统多仓'}${
                c.supports_b ? ' · 含B仓' : ''
              }`}
            />
          ))
        )}
        <LastRow
          label={showForm ? '收起' : '新增渠道'}
          sub="登记后无需发版：下拉自动出现，可复制既有渠道的规则与参数"
          onClick={() => setShowForm(v => !v)}
        />
      </Group>

      {showForm && (
        <Group title="新增渠道">
          <div style={{ padding: '12px 16px', display: 'grid', gap: 10 }}>
            <label style={{ fontSize: 'var(--font-13)', color: 'var(--muted2)' }}>
              渠道标识 code（小写，创建后不可改）
              <input
                value={form.code}
                onChange={e => setForm({ ...form, code: e.target.value })}
                placeholder="如 tmall / pdd / douyin"
                style={inputStyle}
              />
            </label>
            <label style={{ fontSize: 'var(--font-13)', color: 'var(--muted2)' }}>
              显示名
              <input
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="如 天猫"
                style={inputStyle}
              />
            </label>
            <label style={{ fontSize: 'var(--font-13)', color: 'var(--muted2)' }}>
              默认补货模式
              <select
                value={form.default_mode}
                onChange={e => setForm({ ...form, default_mode: e.target.value })}
                style={inputStyle}
              >
                <option value="traditional">传统多仓（逐仓补货）</option>
                <option value="bbcc">BBCC（一盘货：B仓→C仓）</option>
              </select>
            </label>
            <label
              style={{
                fontSize: 'var(--font-13)',
                color: 'var(--muted2)',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <input
                type="checkbox"
                checked={form.supports_b}
                onChange={e => setForm({ ...form, supports_b: e.target.checked })}
              />
              走 BBCC 体系（启用 B 仓调拨链）
            </label>
            <label style={{ fontSize: 'var(--font-13)', color: 'var(--muted2)' }}>
              复制规则与参数自（可选）
              <select
                value={form.copy_from}
                onChange={e => setForm({ ...form, copy_from: e.target.value })}
                style={inputStyle}
              >
                <option value="">不复制（空白渠道）</option>
                {list.map(c => (
                  <option key={c.code} value={c.code}>
                    {c.name}（{c.code}）
                  </option>
                ))}
              </select>
            </label>
            <button className="btn btn-primary" disabled={saving} onClick={create}>
              {saving ? '创建中...' : '创建渠道'}
            </button>
          </div>
        </Group>
      )}

      {list.length > 0 && (
        <Group title="停用渠道">
          {list
            .filter(c => c.enabled !== 0)
            .map(c => (
              <Row
                key={'d' + c.code}
                label={`禁用 ${c.name}`}
                danger
                onClick={() => disable(c.code)}
              />
            ))}
          {list.filter(c => c.enabled === 0).length > 0 &&
            list
              .filter(c => c.enabled === 0)
              .map(c => (
                <Row
                  key={'e' + c.code}
                  label={`重新启用 ${c.name}`}
                  sub={c.code}
                  onClick={() => patch(c.code, { enabled: 1 })}
                />
              ))}
        </Group>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  marginTop: 4,
  padding: '8px 10px',
  fontSize: 'var(--font-13)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  background: 'var(--card)',
  boxSizing: 'border-box',
}
