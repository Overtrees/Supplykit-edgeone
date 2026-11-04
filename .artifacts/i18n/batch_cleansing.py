LOCALE = {
    "cl.custom_field": ("自定义字段", "Custom field"),
    "cl.detect_ing": ("识别中", "Detecting"),
    "cl.detect_failed": ("识别失败", "Detection failed"),
    "cl.detected_n": (
        "已自动识别 {n} 列（未识别的列请在下方手工选择目标字段）",
        "Auto-detected {n} columns (pick target fields for the rest below)",
    ),
    "cl.detect_none": (
        "未自动识别到映射列，请手工选择每列目标字段（选择后系统会记住，下次同列名自动识别）",
        "No columns detected automatically — please map each column by hand (the app remembers it and matches by column name next time)",
    ),
    "cl.req_error": (
        "请求异常: {msg}",
        "Request failed: {msg}",
    ),
    "cl.need_file": ("请先选择文件后再预览", "Select a file before previewing"),
    "cl.previewing": ("预览中", "Previewing"),
    "cl.preview_failed": ("预览失败", "Preview failed"),
    "cl.preview_failed_msg": ("预览失败: {msg}", "Preview failed: {msg}"),
    "cl.req_failed": ("请求失败", "Request failed"),
    "cl.miss_required": (
        "未映射必填列: {cols} —— 请先在下方下拉补映射（带「(必)」后缀的字段），再点预览",
        "Required columns unmapped: {cols} — map them below (fields marked \u201c(required)\u201d) and preview again",
    ),
    "cl.colmap_saved": ("列映射已保存，导入时即时生效", "Column mapping saved — takes effect on the next import"),
    "cl.save_failed": ("保存失败: {msg}", "Save failed: {msg}"),
    "cl.cleansing": ("清洗中...", "Cleansing..."),
    "cl.miss_before_write": (
        "写入前必须映射必填列: {cols}（缺失将导致整批跳过或核算错误），请返回上一步补映射",
        "Map required columns before writing: {cols} (missing ones skip the whole batch or corrupt the calculation). Go back and map them",
    ),
    "cl.need_wh_inv": (
        "导入库存必须映射「仓库」列，否则传统多仓无法按仓库维度核算",
        "Inventory import requires the \u201cwarehouse\u201d column, otherwise traditional multi-warehouse cannot calculate per warehouse",
    ),
    "cl.need_wh_order": (
        "导入订单必须映射「仓库」列，否则传统补货模式按仓库维度核算日销时会丢失该订单销量",
        "Order import requires the \u201cwarehouse\u201d column, otherwise per-warehouse daily sales in traditional mode will miss this order",
    ),
    "cl.need_sku_order": (
        "导入订单必须映射「SKU/商品编号」列（写入唯一键），否则整批订单将被跳过",
        "Order import requires the \u201cSKU / product code\u201d column (part of the unique key), otherwise the whole batch is skipped",
    ),
    "cl.need_expiry": (
        "导入商品必须映射「是否做效期管理」列（填 是/否），否则后续出入库无法判断按效期扣减还是按到货顺序扣减",
        "Product import requires the \u201cexpiry managed\u201d column (yes/no), otherwise inbound/outbound cannot tell whether to deduct by expiry or by arrival order",
    ),
    "cl.need_platform": (
        "导入 GMV 回款必须映射「平台」列（与流水号组成去重键）",
        "GMV import requires the \u201cplatform\u201d column (with the flow number it forms the dedupe key)",
    ),
    "cl.need_flow": (
        "导入 GMV 回款必须映射「流水号」列（与平台组成去重键），否则整批将被跳过",
        "GMV import requires the \u201cflow no.\u201d column (with the platform it forms the dedupe key), otherwise the whole batch is skipped",
    ),
    "cl.dup_all": (
        "同键 {n} 条均已存在，将按本次文件覆盖更新（不会重复计数）",
        "All {n} rows with the same key already exist — this file will overwrite them (no double counting)",
    ),
    "cl.overlap_ask": (
        "其中 {o} 条已存在（将覆盖更新），新增 {n} 条——确认导入？",
        "{o} rows already exist (will be overwritten) and {n} are new — import now?",
    ),
    "cl.submit_failed": ("提交失败", "Submit failed"),
    "cl.imported_before": ("文件已导入过", "This file was already imported"),
    "cl.imported_skip": ("文件已导入过，跳过 {n} 条", "Already imported — skipped {n} rows"),
    "cl.has_duplicates": ("存在重复数据", "Duplicate data found"),
    "cl.done_into": ("清洗完成，数据已归入「{ch}」渠道", "Cleansing done — data filed under \u201c{ch}\u201d"),
    "cl.submitted_n": ("已提交 {n} 条", "Submitted {n} rows"),
    "cl.view_progress": ("查看进度 →", "View progress →"),
    "cl.bg_running": ("导入任务已提交，后台续跑中", "Import submitted — continuing in the background"),
    "cl.failed_msg": ("失败: {msg}", "Failed: {msg}"),
    "cl.progress": (
        "清洗中... {pct}% ({done}/{total}条)",
        "Cleansing... {pct}% ({done}/{total} rows)",
    ),
    "cl.executing": ("执行中", "Running"),
    "cl.submitted_bg": ("已提交，后台续跑中", "Submitted — continuing in the background"),
    "cl.step_upload": ("上传文件", "Upload"),
    "cl.step_map": ("映射字段", "Map fields"),
    "cl.step_preview": ("预览确认", "Preview"),
    "cl.step_done": ("完成", "Done"),
    "cl.pick_type": ("选择导入类型", "Choose import type"),
    "cl.pick_type_sub": (
        "订单 / 库存 / 出入库 / 商品 / 供应商 / GMV 回款",
        "Orders / Inventory / Stock movements / Products / Suppliers / GMV",
    ),
    "cl.opt_order": ("导入订单", "Import orders"),
    "cl.opt_inv_group": ("库存", "Inventory"),
    "cl.opt_inv_own": ("自有仓库存", "Own warehouse stock"),
    "cl.opt_inv_platform": ("平台仓库存", "Platform warehouse stock"),
    "cl.opt_inv_b": ("B仓库存", "B warehouse stock"),
    "cl.opt_flow_group": ("入库出库记录", "Stock movement records"),
    "cl.opt_inbound": ("入库记录", "Inbound records"),
    "cl.opt_outbound": ("出库记录", "Outbound records"),
    "cl.opt_product": ("导入商品", "Import products"),
    "cl.opt_supplier": ("导入供应商", "Import suppliers"),
    "cl.opt_gmv": ("GMV 回款（资金流水）", "GMV settlements (cash flow)"),
    "cl.detecting": ("识别中...", "Detecting..."),
    "cl.detecting_file": ("正在识别文件...", "Detecting file..."),
    "cl.upload_hint": (
        "支持 CSV / Excel · 智能识别列名 · 手工映射为主",
        "CSV / Excel · smart column matching · manual mapping first",
    ),
    "cl.detect_dup": ("检测到重复数据", "Duplicate data detected"),
    "cl.confirm_new": ("确认导入新增", "Import new rows"),
}

REPL = [
    (
        "frontend/src/pages/CleansingPage.tsx",
        """    saveCf([...cf, { t: 'field_' + Date.now(), l: '自定义字段', tp: 'string' }])""",
        """    saveCf([...cf, { t: 'field_' + Date.now(), l: t('cl.custom_field'), tp: 'string' }])""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """    setBs('识别中')""",
        """    setBs(t('cl.detect_ing'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """        toast.error(d.error || '识别失败')""",
        """        toast.error(d.error || t('cl.detect_failed'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """        toast.success(
          '已自动识别 ' + Object.keys(a).length + ' 列（未识别的列请在下方手工选择目标字段）',
        )
      else
        toast.warning(
          '未自动识别到映射列，请手工选择每列目标字段（选择后系统会记住，下次同列名自动识别）',
        )
    } catch (e) {
      toast.error(
        '请求异常: ' +
          e.message +
          (e.response?.data?.detail
            ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
            : ''),
      )
    }""",
        """        toast.success(t('cl.detected_n', { n: Object.keys(a).length }))
      else toast.warning(t('cl.detect_none'))
    } catch (e) {
      toast.error(
        t('cl.req_error', {
          msg:
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        }),
      )
    }""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      toast.error('请先选择文件后再预览')""",
        """      toast.error(t('cl.need_file'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """    setBs('预览中')""",
        """    setBs(t('cl.previewing'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """        toast.error(d.error || '预览失败')
        setBs('')
        return
      }
      // 2026-11-01 必填未映射""",
        """        toast.error(d.error || t('cl.preview_failed'))
        setBs('')
        return
      }
      // 2026-11-01 必填未映射""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          title:
            '未映射必填列: ' +
            _miss.join('、') +
            ' —— 请先在下方下拉补映射（带「(必)」后缀的字段），再点预览',
          duration: 0,""",
        """          title: t('cl.miss_required', { cols: _miss.join('\u3001') }),
          duration: 0,""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      const msg = e.response?.data?.error || e.message || '请求失败'
      toast.error('预览失败: ' + msg)""",
        """      const msg = e.response?.data?.error || e.message || t('cl.req_failed')
      toast.error(t('cl.preview_failed_msg', { msg }))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      toast.success('列映射已保存，导入时即时生效')
      setColMapOpen(false)
    } catch (e) {
      toast.error('保存失败: ' + (e.message || ''))
    }""",
        """      toast.success(t('cl.colmap_saved'))
      setColMapOpen(false)
    } catch (e) {
      toast.error(t('cl.save_failed', { msg: e.message || '' }))
    }""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      setBs('清洗中...')""",
        """      setBs(t('cl.cleansing'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """        toast.error(
          '写入前必须映射必填列: ' +
            _miss.join('、') +
            '（缺失将导致整批跳过或核算错误），请返回上一步补映射',
        )""",
        """        toast.error(t('cl.miss_before_write', { cols: _miss.join('\u3001') }))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error('导入库存必须映射「仓库」列，否则传统多仓无法按仓库维度核算')""",
        """          toast.error(t('cl.need_wh_inv'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error(
            '导入订单必须映射「仓库」列，否则传统补货模式按仓库维度核算日销时会丢失该订单销量',
          )""",
        """          toast.error(t('cl.need_wh_order'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error('导入订单必须映射「SKU/商品编号」列（写入唯一键），否则整批订单将被跳过')""",
        """          toast.error(t('cl.need_sku_order'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error(
            '导入商品必须映射「是否做效期管理」列（填 是/否），否则后续出入库无法判断按效期扣减还是按到货顺序扣减',
          )""",
        """          toast.error(t('cl.need_expiry'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error('导入 GMV 回款必须映射「平台」列（与流水号组成去重键）')""",
        """          toast.error(t('cl.need_platform'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error('导入 GMV 回款必须映射「流水号」列（与平台组成去重键），否则整批将被跳过')""",
        """          toast.error(t('cl.need_flow'))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              toast.warning(
                '同键 ' +
                  (dd.new + dd.overlap || 0) +
                  ' 条均已存在，将按本次文件覆盖更新（不会重复计数）',
              )
            } else if (dd.overlap) {
              setOverlapAsk({
                message:
                  '其中 ' +
                  dd.overlap +
                  ' 条已存在（将覆盖更新），新增 ' +
                  dd.new +
                  ' 条——确认导入？',
              })""",
        """              toast.warning(t('cl.dup_all', { n: dd.new + dd.overlap || 0 }))
            } else if (dd.overlap) {
              setOverlapAsk({
                message: t('cl.overlap_ask', { o: dd.overlap, n: dd.new }),
              })""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.error(d.error || '提交失败')
          setBs('')
          execLock.current = false
          return
        }
        // 同步结果""",
        """          toast.error(d.error || t('cl.submit_failed'))
          setBs('')
          execLock.current = false
          return
        }
        // 同步结果""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """            setRes({ success: d.success, failed: d.failed, message: d.message || '文件已导入过' })
            goStep(3)
            setBs('')
            execLock.current = false
            toast.warning(d.message || '文件已导入过，跳过 ' + d.failed + ' 条')""",
        """            setRes({
              success: d.success,
              failed: d.failed,
              message: d.message || t('cl.imported_before'),
            })
            goStep(3)
            setBs('')
            execLock.current = false
            toast.warning(d.message || t('cl.imported_skip', { n: d.failed }))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """            setOverlapAsk({ message: d.message || '存在重复数据' })""",
        """            setOverlapAsk({ message: d.message || t('cl.has_duplicates') })""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
          return
        }
        const totalRows""",
        """          toast.success(t('cl.done_into', { ch: channelName({ code: ch } as any) }))
          return
        }
        const totalRows""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          message: '已提交 ' + totalRows + ' 条',""",
        """          message: t('cl.submitted_n', { n: totalRows }),""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          title: '已提交 ' + totalRows + ' 条',
          duration: 6000,
          action: {
            label: '查看进度 →',""",
        """          title: t('cl.submitted_n', { n: totalRows }),
          duration: 6000,
          action: {
            label: t('cl.view_progress'),""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              title: '导入任务已提交，后台续跑中',
              duration: 6000,
              action: {
                label: '查看进度 →',""",
        """              title: t('cl.bg_running'),
              duration: 6000,
              action: {
                label: t('cl.view_progress'),""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              toast.success(
                '清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道',
              )
            } else if (sd.status === 'error') {""",
        """              toast.success(t('cl.done_into', { ch: channelName({ code: ch } as any) }))
            } else if (sd.status === 'error') {""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              toast.error('失败: ' + sd.error)""",
        """              toast.error(t('cl.failed_msg', { msg: sd.error }))""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              setBs(
                `清洗中... ${_pct}% (${Math.min((sd.page || 0) * 5000 + 5000, sd.total)}/${sd.total}条)`,
              )""",
        """              setBs(
                t('cl.progress', {
                  pct: _pct,
                  done: Math.min((sd.page || 0) * 5000 + 5000, sd.total),
                  total: sd.total,
                }),
              )""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """        toast.error(
          '请求异常: ' +
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        )
        setBs('')
      }
    } finally {""",
        """        toast.error(
          t('cl.req_error', {
            msg:
              e.message +
              (e.response?.data?.detail
                ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
                : ''),
          }),
        )
        setBs('')
      }
    } finally {""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """    setBs('执行中')
    execLock.current = false""",
        """    setBs(t('cl.executing'))
    execLock.current = false""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      if (!d.ok) {
        toast.error(d.error || '提交失败')
        setBs('')
        return
      }
      if (d.success !== undefined) {
        setRes(d)
        goStep(3)
        setBs('')
        execLock.current = false
        toast.success('清洗完成，数据已归入「' + (ch === 'jd' ? '京东' : '其他渠道') + '」渠道')
        return
      }
      setRes({
        async: true,
        taskId: d.task_id,
        submitted: d.total_rows || d.total || '?',
        message: '已提交 ' + (d.total_rows || d.total || '?') + ' 条',
      })
      goStep(3)
      setBs('')
      execLock.current = false
      toast.success('已提交，后台续跑中')
    } catch (e) {
      toast.error('请求异常: ' + (e.message || ''))
      setBs('')
    }""",
        """      if (!d.ok) {
        toast.error(d.error || t('cl.submit_failed'))
        setBs('')
        return
      }
      if (d.success !== undefined) {
        setRes(d)
        goStep(3)
        setBs('')
        execLock.current = false
        toast.success(t('cl.done_into', { ch: channelName({ code: ch } as any) }))
        return
      }
      setRes({
        async: true,
        taskId: d.task_id,
        submitted: d.total_rows || d.total || '?',
        message: t('cl.submitted_n', { n: d.total_rows || d.total || '?' }),
      })
      goStep(3)
      setBs('')
      execLock.current = false
      toast.success(t('cl.submitted_bg'))
    } catch (e) {
      toast.error(t('cl.req_error', { msg: e.message || '' }))
      setBs('')
    }""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """    setBs('执行中')
    try {
      const r = await uploadRawBytes(
        '/api/cleansing/preview""",
        """    setBs(t('cl.executing'))
    try {
      const r = await uploadRawBytes(
        '/api/cleansing/preview""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """      if (!d.ok) {
        toast.error(d.error || '提交失败')
        setBs('')
        return
      }
      setPv(d)
      doExecute()
    } catch (e) {
      toast.error(
        '请求异常: ' +
          e.message +
          (e.response?.data?.detail
            ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
            : ''),
      )
      setBs('')
    }""",
        """      if (!d.ok) {
        toast.error(d.error || t('cl.submit_failed'))
        setBs('')
        return
      }
      setPv(d)
      doExecute()
    } catch (e) {
      toast.error(
        t('cl.req_error', {
          msg:
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        }),
      )
      setBs('')
    }""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          {['上传文件', '映射字段', '预览确认', '完成'].map((l, i) => (""",
        """          {[t('cl.step_upload'), t('cl.step_map'), t('cl.step_preview'), t('cl.step_done')].map(
            (l, i) => (""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>选择导入类型</div>""",
        """          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
            {t('cl.pick_type')}
          </div>""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """            订单 / 库存 / 出入库 / 商品 / 供应商 / GMV 回款""",
        """            {t('cl.pick_type_sub')}""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              <option value="order">导入订单</option>
              <optgroup label="库存">
                <option value="inventory">自有仓库存</option>
                <option value="platform_inv">平台仓库存</option>
                <option value="inventory_b">B仓库存</option>
              </optgroup>
              <optgroup label="入库出库记录">
                <option value="inbound">入库记录</option>
                <option value="outbound">出库记录</option>
              </optgroup>
              <option value="product">导入商品</option>
              <option value="supplier">导入供应商</option>
              <option value="gmv_flow">GMV 回款（资金流水）</option>""",
        """              <option value="order">{t('cl.opt_order')}</option>
              <optgroup label={t('cl.opt_inv_group')}>
                <option value="inventory">{t('cl.opt_inv_own')}</option>
                <option value="platform_inv">{t('cl.opt_inv_platform')}</option>
                <option value="inventory_b">{t('cl.opt_inv_b')}</option>
              </optgroup>
              <optgroup label={t('cl.opt_flow_group')}>
                <option value="inbound">{t('cl.opt_inbound')}</option>
                <option value="outbound">{t('cl.opt_outbound')}</option>
              </optgroup>
              <option value="product">{t('cl.opt_product')}</option>
              <option value="supplier">{t('cl.opt_supplier')}</option>
              <option value="gmv_flow">{t('cl.opt_gmv')}</option>""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """              <IconLoading size={14} /> 正在识别文件...""",
        """              <IconLoading size={14} /> {t('cl.detecting_file')}""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """            支持 CSV / Excel · 智能识别列名 · 手工映射为主""",
        """            {t('cl.upload_hint')}""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """          title="检测到重复数据"
          desc={overlapAsk.message}
          confirmLabel="确认导入新增\"""",
        """          title={t('cl.detect_dup')}
          desc={overlapAsk.message}
          confirmLabel={t('cl.confirm_new')}""",
    ),
    (
        "frontend/src/pages/CleansingPage.tsx",
        """import { t } from '../locale'""",
        """import { t } from '../locale'
import { channelName } from '../components/channels'""",
    ),
]
