import { describe, it, expect } from 'vitest'
import {
  taskTypeOf,
  TYPE_LABEL_KEY,
  WHAT_LABEL_KEY,
  type TaskType,
} from '../components/tasks/taskType'
import { zh } from '../locale.zh'

/**
 * §15.204 回归: 任务卡把「规则评估」显示成了「数据清洗导入」。
 *
 * 根因不是漏了一条文案, 而是**兜底方向错了** —— 原实现「白名单匹配 → 兜底 cleansing」,
 * 任何新 task_type 都会被冒充成导入。这批断言锁的是"兜底不许冒充", 而不是
 * "rule_eval 必须有名字"(后者加个 map 就过了, 下次新增类型照样错)。
 */
describe('task_type 展示类型', () => {
  it('rule_eval 不再被兜底成 cleansing', () => {
    // 这条守的是"rule_eval 有专属分支"(删掉分支它必红); 它**抓不到**"兜底写错",
    // 因为显式分支先命中 —— 所以下面第 3 条才是兜底守卫, 两条缺一不可。
    expect(taskTypeOf({ task_type: 'rule_eval', task_id: 'ev_1794804393_6117' })).toBe('rule_eval')
  })

  it('已知类型逐个精确匹配', () => {
    expect(taskTypeOf({ task_type: 'cleansing' })).toBe('cleansing')
    expect(taskTypeOf({ task_type: 'cleansing_eval' })).toBe('cleansing_eval')
    expect(taskTypeOf({ task_type: 'export' })).toBe('export')
    expect(taskTypeOf({ task_type: 'seed' })).toBe('seed')
    expect(taskTypeOf({ task_type: 'reset' })).toBe('reset')
  })

  it('认不出的类型兜底 unknown, 绝不冒充 cleansing', () => {
    // 未来新增类型忘登记时, 显示「任务」而不是谎报成导入
    for (const tt of ['whatever_new_type', '', 'CLEANSING', 'clean']) {
      const v = taskTypeOf({ task_type: tt })
      expect(v).toBe('unknown')
      expect(v).not.toBe('cleansing')
    }
    // 完全没有 task_type 字段
    expect(taskTypeOf({})).toBe('unknown')
  })

  it('reset 可只靠 id 前缀识别(一次性任务无 task_type)', () => {
    expect(taskTypeOf({ task_id: 'reset_1794800000_1234' })).toBe('reset')
  })

  it('每个展示类型都有标题文案键, 且都能解析出非空文案', () => {
    for (const k of Object.keys(TYPE_LABEL_KEY) as TaskType[]) {
      const key = TYPE_LABEL_KEY[k]
      expect(key).toMatch(/^task\./)
      // 键确实存在于中文字典(防止只加了 map 没加词条 → 界面显示原始 key)
      expect(Object.prototype.hasOwnProperty.call(zh, key)).toBe(true)
      expect(String(zh[key]).length).toBeGreaterThan(0)
    }
  })

  it('完成提示缺项时回退到标题键, 回退目标必须存在', () => {
    for (const k of Object.keys(WHAT_LABEL_KEY) as TaskType[]) {
      const key = WHAT_LABEL_KEY[k] as string
      expect(Object.prototype.hasOwnProperty.call(zh, key)).toBe(true)
    }
    // reset / unknown 没有专属短文案 → 页面回退 TYPE_LABEL_KEY[k], 该键必须存在
    for (const k of ['reset', 'unknown'] as TaskType[]) {
      expect(TYPE_LABEL_KEY[k]).toBeTruthy()
    }
  })

  it('评估类的两个 task_type 共用标题, 副标题可区分', () => {
    // 对用户是同一件事(规则评估), 但一个全量一个增量 —— 标题同、副标题不同
    expect(TYPE_LABEL_KEY.rule_eval).toBe(TYPE_LABEL_KEY.cleansing_eval)
    expect(zh['task.eval_full']).toBeTruthy()
    expect(zh['task.eval_delta']).toBeTruthy()
    expect(zh['task.eval_full']).not.toBe(zh['task.eval_delta'])
  })
})
