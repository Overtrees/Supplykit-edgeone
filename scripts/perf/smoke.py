# -*- coding: utf-8 -*-
"""接口冒烟（只读、串行、零写）—— 尤其用于 **DB 不可用** 时判断失败面。

与压测的区别
------------
`run.py` 测「快不快 / 停不停摆」，需要 DB 正常、要判据、要多轮。
本脚本测「正不正常」，只发一轮 GET，**DB 挂掉时也能跑**，用来回答：
  · 哪些接口还能返回有效数据
  · 哪些明确报 DB 错误
  · 哪些**静默返回 200 + 全 0 空壳**（最危险：前端显示「暂无数据」而非报错）

接口清单来自 `endpoints.py`（唯一真源），另加 3 个探活端点；不许手抄。

为什么用 demo 账号
------------------
`routes/auth.login` 里 demo 是内置硬编码分支（不查 users 表），DB 挂掉时仍能
拿到 token，因此能把「鉴权级联 401」与「接口本身挂」分开。只读 GET，不发写。

红线
----
· 不并发、不发写（集群被限流时避免加压）
· 不做延迟结论，只做可用性判定（红线 7 / 17）
· 单请求 25s 超时，失败即记，不重试

用法
----
    python3 scripts/perf/smoke.py                       # 打生产，结果写 scripts/perf/out/
    python3 scripts/perf/smoke.py --base http://...     # 换环境
    python3 scripts/perf/smoke.py --out /tmp/s.json     # 指定输出
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endpoints import ENDPOINTS  # noqa: E402  唯一真源

BASE_DEFAULT = "https://supplykit.top"
TIMEOUT = 25

# 探活/鉴权端点不查业务库，单列（它们不在 ENDPOINTS 里）
PROBES = {
    "health": "/api/health",
    "ping": "/api/insights/ping",
    "auth_check": "/api/auth/check",
}

H = {"X-Accept-Encoding": "gzip", "Accept-Encoding": "gzip, deflate, br"}


def _has_payload(v, key=""):
    """递归判断响应里是否含**有效业务数据**。

    DB 挂掉时多个接口返回 200 + 全 0 / 空列表的空壳（前端会显示「暂无数据」，
    比报 500 更误导）。把「配置型常量」与「业务数据」分开：
      · 计数 / 非空列表 / archive、orders 非 null → 有数据
      · cutoff、archive_day、*_days、channel、mode 等是常量，不算
      · 含「暂无」的兜底文案不算（hist_coverage 的 DB 失败文案正是这种）
    """
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return v > 0 and not key.endswith("_days")
    if isinstance(v, str):
        if not v or "暂无" in v or "无归档" in v:
            return False
        return key not in ("cutoff", "archive_day", "note", "channel", "mode",
                           "built_at", "timing", "risk_stat", "fp_head", "fp_now")
    if isinstance(v, list):
        return len(v) > 0
    if isinstance(v, dict):
        return any(_has_payload(x, k) for k, x in v.items())
    return False


def classify(name, r):
    """返回 (类别, 摘要)。类别：A 可用 / E 静默空壳 / C 明确失败 / 5xx / 401"""
    txt = r.text or ""
    ct = r.headers.get("content-type", "")
    if r.status_code == 401:
        return "401", "未登录/令牌无效"
    if r.status_code == 403:
        return "403", "访客只读限制"
    if r.status_code >= 500:
        return "5xx", "HTTP %s %s（网关/平台错误页，非应用 JSON）" % (r.status_code, ct[:30])
    if not ct.startswith("application/json"):
        return "非JSON", "%s %s" % (r.status_code, txt[:80].replace("\n", " "))
    try:
        j = r.json()
    except Exception:
        return "非JSON", txt[:100].replace("\n", " ")

    # health 是探针：body 里本来就带 quota 字样，但它**明确报了降级**，属于可用
    if name == "health":
        return ("A:可用(降级)" if j.get("status") == "degraded" else "A:可用"), \
               "status=%s db=%s" % (j.get("status"), str(j.get("db"))[:60])
    # 嵌套失败也算（boot 的 data 里每个子块都是 OperationalError）
    if "operationalerror" in txt.lower() or "1105" in txt or "quota" in txt.lower():
        return "C:DB失败", "响应体内含 OperationalError/1105/quota（含嵌套）"
    if j.get("ok") is False and not j.get("__swr"):
        return "C:失败", str(j.get("error") or "")[:100]
    # ping / auth_check 是探活与鉴权端点，本就不带 data
    if name in ("ping", "auth_check"):
        return "A:可用", "ok=%s 无数据体(设计如此)" % j.get("ok")

    d = j.get("data")
    # unit_state 是诊断口：cold_guard.started 等是**进程内累计计数器**，不是业务数据。
    # 它的真值只看 gen/units（DB 挂掉时两者都是 0）。
    if name == "unit_state" and isinstance(d, dict):
        if not (int(d.get("gen") or 0) > 0 and int(d.get("units") or 0) > 0):
            return "E:静默空壳", "gen/units=0（cold_guard 计数是进程累计值，非业务数据）"
        return "A:可用", "gen=%s units=%s" % (d.get("gen"), d.get("units"))
    payload = _has_payload(d)
    swr = bool(j.get("__swr"))
    if not payload:
        return "E:静默空壳", "HTTP 200 但业务数据全 0/空（前端会显示「暂无数据」）"
    return ("B:降级(SWR)" if swr else "A:可用"), "有效数据=%s swr=%s" % (payload, swr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("SMOKE_BASE", BASE_DEFAULT))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    out_path = a.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out",
        "smoke_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    ep = dict(PROBES)
    ep.update({k: v["path"] for k, v in ENDPOINTS.items()})

    s = requests.Session()
    s.headers.update(H)
    t0 = time.time()
    try:
        lr = s.post(a.base + "/api/auth/login",
                    json={"username": "demo", "password": "demo123"}, timeout=TIMEOUT)
        lj = lr.json() if lr.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        lr, lj = None, {}
        print("LOGIN 异常:", type(e).__name__, str(e)[:120])
    tok = lj.get("token")
    print("LOGIN(demo) %s token=%s" % (lr.status_code if lr is not None else -1, bool(tok)))
    if tok:
        s.headers.update({"X-Auth-Token": tok})

    rows, summary = [], {}
    for name, path in ep.items():
        t = time.time()
        deg = False
        try:
            r = s.get(a.base + path, timeout=TIMEOUT)
            cat, brief = classify(name, r)
            # 2026-11-09 §8.7: 后端统一降级头。只做展示标注, **不改分类判据**。
            deg = r.headers.get("X-App-Degraded") == "1"
            rows.append(dict(ep=name, http=r.status_code, srv=r.headers.get("X-Srv-Ms"),
                             e2e=round(time.time() - t, 2), cat=cat, brief=brief,
                             degraded=deg, body=r.text[:220]))
        except Exception as e:
            cat, brief = "C:异常", "%s %s" % (type(e).__name__, str(e)[:90])
            rows.append(dict(ep=name, http=-1, srv=None, e2e=round(time.time() - t, 2),
                             cat=cat, brief=brief, body=""))
        summary[cat.split(":")[0]] = summary.get(cat.split(":")[0], 0) + 1
        print("%-16s %-14s %s%s" % (name, cat, brief[:80],
                                    " [DEGRADED]" if deg else ""), flush=True)

    res = dict(ts=time.strftime("%Y-%m-%d %H:%M:%S"), base=a.base,
               login=dict(http=lr.status_code if lr is not None else -1, token=bool(tok)),
               summary=summary, total=len(rows), rows=rows,
               elapsed=round(time.time() - t0, 1))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("\nSUMMARY", json.dumps(summary, ensure_ascii=False), "total", len(rows),
          "elapsed", res["elapsed"], "->", out_path)


if __name__ == "__main__":
    main()
