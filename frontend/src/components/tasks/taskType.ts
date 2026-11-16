/**
 * task_type → 展示类型 / 文案键。**纯函数, 无 React 依赖**, 便于单测。
 *
 * 2026-11-16 §15.204 根因: 这里原来是被内联在 TaskPage 里的三段嵌套三元 ——
 *   「白名单匹配 → 兜底 'cleansing'」。新增的 `rule_eval`(每日评估接力) 不匹配任何
 *   分支 → 被兜底成导入 → 任务卡显示**「数据清洗导入」**。
 * 用户看到规则评估叫导入, 会去查"我什么时候导入了数据", 这是把人往错方向带。
 *
 * 三条纪律:
 *   1. **兜底只能是 'unknown'**。新增 task_type 忘了登记 → 显示「任务」, 不冒充别人。
 *   2. `cleansing` 显式登记, 不再靠兜底 —— 兜底能兜住它, 正是这个 bug 的来源。
 *   3. 本模块是 task_type → 展示的唯一真源。渲染与完成提示共用, 不许各自再写一份
 *      (原 toast 分支判断 `task_type === 'clean'` 而实际值是 `'cleansing'` →
 *       该分支永不命中, 完成提示一直显示英文原文 `rule_eval`)。
 *
 * 与 `api/taskWatch._kindOf` 的关系: 那个函数决定「用哪个端点推进任务」, 是执行路由;
 *   本模块决定「怎么显示」, 是展示。两者职责不同, 不合并 —— 但**都必须显式白名单 +
 *   认不出不接管/不冒充**, 这条纪律同源(见 core/eval_kick.py:15 的事故记录)。
 */

export type TaskType =
  'cleansing' | 'cleansing_eval' | 'rule_eval' | 'export' | 'seed' | 'reset' | 'unknown'

export interface TaskLike {
  task_type?: string
  task_id?: string
}

export function taskTypeOf(task: TaskLike): TaskType {
  const tt = String(task.task_type || '')
  if (tt === 'cleansing') return 'cleansing'
  if (tt === 'cleansing_eval') return 'cleansing_eval'
  // `rule_eval` 与 `cleansing_eval` 对用户是同一件事(规则评估), 只是触发方式不同:
  //   前者 = 每日/手动全量接力(core/rule_eval_task.py); 后者 = 导入后增量联动。
  //   标签共用「评估联动」, 副标题区分(全量 / 评估联动)。
  if (tt === 'rule_eval') return 'rule_eval'
  if (tt === 'export') return 'export'
  if (tt === 'seed') return 'seed'
  if (tt === 'reset') return 'reset'
  // 一次性 reset 任务有些不带 task_type, 只在 id 上带前缀
  if (String(task.task_id || '').startsWith('reset_')) return 'reset'
  return 'unknown'
}

/** 卡片标题 */
export const TYPE_LABEL_KEY: Record<TaskType, string> = {
  cleansing: 'task.type_cleansing',
  cleansing_eval: 'task.type_eval',
  rule_eval: 'task.type_eval',
  export: 'task.type_export',
  seed: 'task.type_seed',
  reset: 'task.type_reset',
  unknown: 'task.type_unknown',
}

/** 完成提示里的「什么任务」—— 措辞比标题短。缺项回退到 TYPE_LABEL_KEY。 */
export const WHAT_LABEL_KEY: Partial<Record<TaskType, string>> = {
  seed: 'task.what_seed',
  cleansing: 'task.what_cleansing',
  cleansing_eval: 'task.what_eval',
  rule_eval: 'task.what_eval',
  export: 'task.what_export',
}
