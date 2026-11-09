#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6-6 / P6-7 判据守门自检 —— **零 RU**（不发任何网络请求，全部打桩）。

跑法：``python3 scripts/perf/p66_selftest.py``   退出码 0 = 全部 PASS。

守的是 §15.196 排查出、本轮已修的三条真实缺陷：

1. ``hit()`` 的 ``complete`` 未带 HTTP 200 前置 → ``alerts`` 对「500 + ``{"data":[]}``」免疫，
   三条 storm 判据全过（**真实假 PASS 路径**，违反红线 8①）；
2. 非 200 只按 ``app_headers`` 归因 → 应用未捕获的 500（ServerErrorMiddleware，绕开 http
   中间件）被误判成"边缘白页"；
3. ``pct()`` 剔除 ``srv<0`` 却不报数 → p50/p95 是"成功请求的"分位数（幸存者偏差不可见）。

设计约束：纯函数 + 打桩，**不连 DB、不发 HTTP、不产出任何性能结论**；用例里带反证，
证明断言确实咬在缺陷上（旧实现在同一输入上给出相反结论）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kit as K                     # noqa: E402
import scenes as SC                 # noqa: E402
from endpoints import complete as _complete    # noqa: E402

_OK, _NG = [], []


def ck(name, cond, detail=""):
    (_OK if cond else _NG).append(name)
    tail = "" if cond else ("  ← 实测 %r" % (detail,))
    print(("  PASS " if cond else "  FAIL ") + name + tail, flush=True)


class _Resp:
    """最小 requests.Response 替身（只提供 hit() 用到的字段）。"""

    def __init__(self, status, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self._text = text if text is not None else (
            "" if payload is None else __import__("json").dumps(payload))
        self.content = self._text.encode("utf-8")
        self.headers = headers or {}

    @property
    def text(self):
        return self._text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _Sess:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    def get(self, *a, **kw):
        if self._exc is not None:
            raise self._exc
        return self._resp


def _hit(resp=None, exc=None, key="alerts"):
    K.sess = lambda: _Sess(resp, exc)      # 打桩：不发请求
    return K.hit(key)


HTML = "<!doctype html><html><head><title>Tencent Cloud</title></head><body>...</body></html>"
JSON_500 = {"ok": False, "error": "服务器内部错误"}
JSON_BUSY = {"ok": False, "error": "数据库繁忙，请稍后重试", "retryable": True}


def main():
    print("== 1. 来源归类 _classify（缺口 A） ==")
    table = [
        ("503 + HTML 白页、无 X-Srv-Ms", 503, -1, HTML, "edge"),
        ("503 + X-Srv-Ms（应用自己返回）", 503, 6120, '{"ok":false}', "app"),
        ("500 + JSON、无 X-Srv-Ms（应用未捕获）", 500, -1, '{"ok":false}', "app_uncaught"),
        ("500 + 空 body、无 X-Srv-Ms", 500, -1, "", "app_uncaught"),
        ("连接层失败（无 HTTP 响应）", -1, -1, "", "net"),
    ]
    for name, st, srv, body, want in table:
        got = K._classify(st, srv, body)
        ck("_classify %-34s → %s" % (name, want), got == want, got)

    print("== 2. complete 带 200 前置（缺口 C：假 PASS 路径） ==")
    h = _hit(_Resp(200, {"data": []}))
    ck("alerts 200 + {\"data\":[]} → 完整（白名单只在 200 时生效）",
       h["complete"] is True, h["complete"])

    h = _hit(_Resp(500, {"data": []}))
    ck("alerts 500 + {\"data\":[]} → **不完整**（P6-6 已堵）",
       h["complete"] is False, h["complete"])
    ck("alerts 500 + {\"data\":[]} → src=app_uncaught",
       h.get("src") == "app_uncaught", h.get("src"))

    h = _hit(_Resp(502, {"data": []}))
    ck("alerts 502 + {\"data\":[]} → **不完整**", h["complete"] is False, h["complete"])

    h = _hit(_Resp(200, {"data": [{"id": 1}]}))
    ck("alerts 200 + 有数据 → 完整", h["complete"] is True, h["complete"])

    # 反证：裸 complete() 在同一响应上仍返回 True —— 证明修复确实咬在缺陷上
    ck("反证：裸 complete() 对 500 的空 data 仍判 True", _complete("alerts", []) is True,
       _complete("alerts", []))

    print("== 3. 白页 vs 应用 503 的区分（缺口 A/D） ==")
    h = _hit(_Resp(503, None, text=HTML))
    ck("503 HTML → src=edge", h.get("src") == "edge", h.get("src"))
    ck("503 HTML → app_headers=False", h.get("app_headers") is False, h.get("app_headers"))
    ck("503 HTML → 不完整", h["complete"] is False, h["complete"])
    ck("503 HTML → body 截断保留 title", "Tencent" in h.get("body", ""), h.get("body", "")[:60])

    h = _hit(_Resp(503, JSON_BUSY, headers={"X-Srv-Ms": "6012"}))
    ck("503 带 X-Srv-Ms → src=app", h.get("src") == "app", h.get("src"))
    ck("503 带 X-Srv-Ms → app_headers=True", h.get("app_headers") is True, h.get("app_headers"))
    ck("503 带 X-Srv-Ms → srv 取到 6012（不当白页算）", h["srv"] == 6012, h["srv"])

    print("== 4. 应用未捕获 500 不被误判为边缘（缺口 A 的核心） ==")
    h = _hit(_Resp(500, JSON_500))
    ck("500 JSON 无 X-Srv-Ms → src=app_uncaught（≠edge）",
       h.get("src") == "app_uncaught", h.get("src"))

    print("== 5. 连接异常 ==")
    h = _hit(exc=TimeoutError("read timeout"))
    ck("异常 → src=net", h.get("src") == "net", h.get("src"))
    ck("异常 → srv=-1 / complete=False", h["srv"] == -1 and h["complete"] is False, h["srv"])

    print("== 6. _summary 分层统计（缺口 D/E） ==")
    hits = [
        {"ep": "a", "srv": 100, "status": 200, "rows": 5, "swr": False, "complete": True},
        {"ep": "a", "srv": -1, "status": 500, "rows": -1, "swr": False, "complete": False,
         "src": "edge", "t": 0.84, "body": HTML[:40], "app_headers": False},
        {"ep": "b", "srv": -1, "status": -1, "rows": -1, "swr": False, "complete": False,
         "src": "net", "err": "timeout"},
    ]
    s = SC._summary(hits)
    ck("bad_src 按来源分层计数", s["bad_src"] == {"edge": 1, "net": 1}, s["bad_src"])
    ck("srv_failed 显式报出被剔除 2 条", s["srv_failed"] == 2, s["srv_failed"])
    ck("p50 只由成功请求决定（100）", s["srv_p50"] == 100, s["srv_p50"])
    ck("n 仍是全量（3，分母不缩水）", s["n"] == 3, s["n"])
    ck("bad[] 每条带 src", all("src" in b for b in s["bad"]), s["bad"])
    ck("non200=2 / status503=0（500 不冒充 503）",
       s["non200"] == 2 and s["status503"] == 0, (s["non200"], s["status503"]))
    ck("incomplete=2", s["incomplete"] == 2, s["incomplete"])

    print("== 7. 判据真源未被本轮改动（红线：不许改判据转绿） ==")
    import run as R
    ck("TH.storm_503 == 0", R.TH["storm_503"] == 0, R.TH["storm_503"])
    ck("TH.storm_incomplete == 0", R.TH["storm_incomplete"] == 0, R.TH["storm_incomplete"])
    ck("TH.storm_max == 10000", R.TH["storm_max"] == 10000, R.TH["storm_max"])
    ck("TH.hot_p95 == 1500", R.TH["hot_p95"] == 1500, R.TH["hot_p95"])
    ck("TH.hot_p50 == 800", R.TH["hot_p50"] == 800, R.TH["hot_p50"])
    ck("TH.hot_cv == 0.20", R.TH["hot_cv"] == 0.20, R.TH["hot_cv"])

    n = len(_OK) + len(_NG)
    print("\n%s %d/%d PASS" % ("ALL PASS" if not _NG else "FAILED", len(_OK), n))
    if _NG:
        for name in _NG:
            print("  ✗ " + name)
    return 0 if not _NG else 1


if __name__ == "__main__":
    sys.exit(main())
