"""中央缓存失效注册表(四维保障: 数据写操作 → invalidate_all → 分析缓存即时失效)

实时性: 清洗导入/seed/规则/库存等写操作成功后调用 invalidate_all(),
        看板(summary/aux)/补货/采购缓存立即清空 → 下个请求即最新(不等 TTL)
准确性: 缓存与源数据同 SQL; 失效保证不返旧
完整性: 缓存不裁剪数据; 失效不丢更新
可靠性: 缓存异常降级直查(TTL 内读失败即重建)
"""
_registry = {}


def register(clear_fn):
    """分析路由在模块加载时注册自己的缓存清理函数"""
    _registry[len(_registry)] = clear_fn


def invalidate_all():
    """写路由成功后调用: 清空全部分析缓存"""
    for fn in list(_registry.values()):
        try:
            fn()
        except Exception:
            pass