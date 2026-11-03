#!/usr/bin/env python3
"""CI 启动时解析数据库连接 —— 单一真相源 = EdgeOne 项目环境变量

为什么要有它(2026-11-03):
    GitHub Secrets 里那套 TIDB_* 与 EdgeOne 项目里配的那套**是两份独立的副本**,
    改了一边不会同步另一边。上线半年后 CI 首跑 SQL 冒烟就撞上了: 60 条 SQL 一起
    报 1146 表不存在, 看起来像 60 个代码缺陷, 实际是 CI 连到了另一个库。
    两处维护的凭据迟早会漂移, 区别只是漂了之后花多久才发现。

做法:
    CI 里只留一个 EdgeOne API Token, 其余连接参数运行时现取 —— 线上用什么, CI 就
    验什么。轮换密码只改 EdgeOne 一处, CI 自动跟随。

输出(供 `>> $GITHUB_ENV`):
    ::add-mask::<value>      先把值登记为掩码, 后续日志里出现也只会显示 ***
    TIDB_HOST=...            直接写进 GITHUB_ENV, 后续 step 可见

降级:
    API 拉不到(网络/token 失效/fork PR 无 secret) → 回落 GitHub Secrets 那套,
    并打 ::warning:: 说明本轮用的是旧副本。**不失败** —— 回归本身不依赖数据库,
    拿不到凭据只该让 SQL 冒烟跳过, 不该让整个门禁红。

用法:
    python3 scripts/ci_fetch_env.py                        # CI 里跑
    python3 scripts/ci_fetch_env.py --show                 # 本地看一眼(值打码)
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://pages-api.cloud.tencent.com/v1"
# 只取这几个键: 其余(CRON_SECRET/JWT_SECRET 等)与 CI 无关, 少拿一个少一分泄露面
WANT = ("TIDB_HOST", "TIDB_PORT", "TIDB_USER", "TIDB_PASSWORD", "TIDB_DB", "TIDB_SSL")
SHOW = "--show" in sys.argv
_SECRET_KEYS = {"TIDB_PASSWORD"}


def project_id():
    p = os.path.join(ROOT, ".edgeone", "project.json")
    try:
        return json.load(open(p, encoding="utf-8")).get("ProjectId")
    except Exception:
        return None


def fetch_envs(token, pid):
    body = json.dumps({"Action": "DescribePagesProjectEnvs", "ProjectId": pid}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    r = json.loads(urllib.request.urlopen(req, timeout=25).read())
    if r.get("Code") != 0:
        raise RuntimeError("API Code=%s %s" % (r.get("Code"), str(r.get("Message"))[:120]))
    resp = (r.get("Data") or {}).get("Response") or {}
    out = {}
    for e in resp.get("EnvVars") or []:
        k = e.get("Key")
        # 只要 Production 档: Preview 可能是另一套库, CI 验的应该是线上那套
        if k in WANT and "Production" in (e.get("Env") or []):
            out[k] = str(e.get("Value") or "")
    return out


def fallback():
    """GitHub Secrets 副本(由 workflow 以 FB_ 前缀传入)。"""
    m = {"FB_HOST": "TIDB_HOST", "FB_PORT": "TIDB_PORT", "FB_USER": "TIDB_USER",
         "FB_PASS": "TIDB_PASSWORD", "FB_DB": "TIDB_DB", "FB_SSL": "TIDB_SSL"}
    return {dst: os.environ.get(src, "") for src, dst in m.items() if os.environ.get(src)}


def emit(pairs):
    """写 GITHUB_ENV 文件, 不走 stdout。

    原因: workflow 命令(`::add-mask::` / `::warning::`)必须出现在 stdout 才会被
    runner 解析。若调用方写成 `python3 x.py >> $GITHUB_ENV`, 命令会连同
    `KEY=value` 一起被塞进环境文件 —— 掩码失效(密码进日志)且变量名非法。
    """
    dest = os.environ.get("GITHUB_ENV")
    lines = ["%s=%s" % (k, v) for k, v in pairs.items()]
    if dest:
        with open(dest, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        for k, v in pairs.items():
            # 只掩密码: 把 `4000` / `supplykit` / `true` 也登记成掩码, 会把日志里所有
            # 端口号和库名一起打成 ***, 连"连的是哪个库"都看不出来 —— 那正是上次
            # 排查 CI 时唯一缺的线索。
            if v and k in _SECRET_KEYS:
                print("::add-mask::%s" % v)
    else:
        for ln in lines:
            print(ln)


def main():
    token = os.environ.get("EDGEONE_PAGES_API_TOKEN", "").strip()
    pid = os.environ.get("EDGEONE_PROJECT_ID") or project_id()
    envs, why = {}, ""
    if not token:
        why = "无 EDGEONE_PAGES_API_TOKEN"
    elif not pid:
        why = "无 ProjectId(.edgeone/ 被 gitignore, CI 需显式传 EDGEONE_PROJECT_ID)"
    else:
        try:
            envs = fetch_envs(token, pid)
            if not envs.get("TIDB_HOST"):
                envs, why = {}, "EdgeOne 未返回 TIDB_HOST"
        except Exception as e:
            envs, why = {}, "%s: %s" % (type(e).__name__, str(e)[:100])

    if envs:
        src = "edgeone"
        pairs = envs
    else:
        pairs = fallback()
        src = "secrets" if pairs else "none"
        if pairs:
            print("::warning title=用回落的旧凭据::%s, 本轮 SQL 冒烟跑的是 GitHub Secrets 副本;"
                  " 它可能与线上不一致, 修法见 docs/DEVELOPMENT.md" % why)
        else:
            print("::warning title=无数据库凭据::%s, 且无回落 secrets —— 本轮跳过 SQL 冒烟" % why)

    if SHOW:
        for k in WANT:
            v = pairs.get(k, "")
            if "PASSWORD" in k and v:
                v = "***"
            print("  %-14s %s" % (k, v or "(缺)"))
        print("  来源:", src)
        return 0

    print("CI_ENV_SOURCE=%s" % src)
    emit({k: pairs.get(k, "") for k in WANT})
    # 只打非敏感项, 让 CI 日志能直接看出"连的是哪个库" —— 上次就是这里没有线索
    print("── 数据库连接来源: %s ──" % src, file=sys.stderr)
    print("   host=%s db=%s user=%s" % (pairs.get("TIDB_HOST", "?"), pairs.get("TIDB_DB", "?"),
                                        (pairs.get("TIDB_USER", "?") or "?")[:6] + "***"),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
