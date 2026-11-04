"""渠道展示名的 i18n 键映射(红线 B: 界面文案不硬编码中文)。

为什么放在后端而不是前端写死:
  `channels` 表里的 name 是**数据**(用户可改、可新建自定义渠道), 前端不能拿 code 硬编码
  中文名 —— 但那两条内置渠道(jd/other)的名字又确实是系统给的, 不是用户填的。
  所以后端在下发时补一个 `name_key`, 前端 `t(name_key)` 出双语; 用户自建的渠道没有
  name_key, 前端回落显示 name 原文(那是用户自己的数据, 不做翻译)。

维护: 新增内置渠道 → 在 _KEYS 里加一对(code → i18n 键), 并同步 locale.zh/en 词条。
"""

_KEYS = {
    "jd": "channel.jd",
    "other": "channel.other",
}


def channel_name_key(code):
    """内置渠道 → i18n 键; 未登记(自定义渠道) → 空串, 前端回落 name"""
    return _KEYS.get(str(code or ""), "")
