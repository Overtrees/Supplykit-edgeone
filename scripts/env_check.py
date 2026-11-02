#!/usr/bin/env python3
"""环境自检: 一条命令看清「这台机器能不能改码 / 连库 / 查部署 / 推代码」

背景: 换机或新会话接手时, 缺的往往不是代码, 而是三样凭据 —— .env(TiDB)、
.edgeone(Git 集成项目的部署状态查询)、git push 权限(Git 集成项目的部署触发器)。
以前是踩到了才发现(改完代码准备推才发现没权限), 这里提前一次性体检。

用法:
    python3 scripts/env_check.py           # 全量体检
    python3 scripts/env_check.py --no-net  # 跳过联网检查(只查本地文件/依赖)

判读:
    全部 ✓ → 可以改码 / 本地直连 / 查部署状态 / 推代码
    ✗ DB   → 只能改码 + 本地 mock 回归(local_test.py), 不能验真实数据
    ✗ DEPLOY → 改完无法确认线上跑的是哪个 commit
    ✗ PUSH → 无法触发部署(Git 集成项目靠 push 触发 webhook)

退出码: 0 全通过 / 1 有缺失
"""
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_NET = "--no-net" in sys.argv
OK, BAD = "✓", "✗"
rows = []          # (项, 状态, 说明)
hints = []


def _w(s):
    """显示宽度: CJK 全角按 2 列算(否则表格错位)"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def add(name, ok, detail, hint=None):
    rows.append((name, OK if ok else BAD, detail))
    if not ok and hint:
        hints.append(hint)


def load_env(path):
    if not os.path.exists(path):
        return False
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return True


def run(cmd, timeout=15, env=None):
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:  # 超时/命令不存在
        return 1, "", str(e)


# ── 1. Python 依赖(本地回归与直连都靠它) ──
missing = [m for m in ("pymysql", "fastapi") if importlib.util.find_spec(m) is None]
add("Python 依赖", not missing,
    "pymysql / fastapi 就绪" if not missing else "缺 %s" % ", ".join(missing),
    "pip3 install pymysql fastapi python-multipart")

# ── 2. .env 与 TiDB 连通 ──
has_env = load_env(os.path.join(ROOT, ".env"))
need = ["TIDB_HOST", "TIDB_USER", "TIDB_PASSWORD", "TIDB_DB"]
lack = [k for k in need if not os.environ.get(k)]
if not has_env:
    add(".env 配置", False, "文件不存在", "cp .env.example .env 后填 TIDB_HOST/USER/PASSWORD(控制台 Connect 页可取)")
elif lack:
    add(".env 配置", False, "缺 %s" % ", ".join(lack), "补 .env: %s" % ", ".join(lack))
else:
    add(".env 配置", True, "%s / %s" % (os.environ.get("TIDB_HOST", "")[:28], os.environ.get("TIDB_DB")))

db_ok = False
if has_env and not lack and not NO_NET:
    try:
        import pymysql
        conn = pymysql.connect(host=os.environ["TIDB_HOST"], port=int(os.environ.get("TIDB_PORT", "4000")),
                               user=os.environ["TIDB_USER"], password=os.environ["TIDB_PASSWORD"],
                               database=os.environ.get("TIDB_DB", "supplykit"),
                               connect_timeout=8, read_timeout=8)
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE(), VERSION()")
            row = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM products")
            n = cur.fetchone()[0]
        conn.close()
        db_ok = True
        add("TiDB 连通", True, "%s · products %s 行" % (row[0], n))
    except Exception as e:
        msg = str(e).split("\n")[0][:90]
        add("TiDB 连通", False, msg, "核对 TIDB_HOST/PASSWORD; 控制台确认 IP 白名单已放行当前出口 IP")
elif NO_NET:
    add("TiDB 连通", True, "已跳过(--no-net)")
else:
    add("TiDB 连通", False, "无凭据, 未尝试")

# ── 3. 部署状态查询凭据(.edgeone, 不入库) ──
tok = os.environ.get("EDGEONE_PAGES_API_TOKEN")
pid = None
auth_f = os.path.join(ROOT, ".edgeone", "auth.json")
proj_f = os.path.join(ROOT, ".edgeone", "project.json")
if os.path.exists(auth_f):
    try:
        tok = tok or json.load(open(auth_f)).get("EDGEONE_PAGES_API_TOKEN")
    except Exception:
        pass
if os.path.exists(proj_f):
    try:
        pid = json.load(open(proj_f)).get("ProjectId")
    except Exception:
        pass

if not tok:
    add("部署状态凭据", False, "无 EDGEONE_PAGES_API_TOKEN",
        "控制台 Makers → Settings → API Token 生成; 写入 .edgeone/auth.json 或环境变量")
else:
    add("部署状态凭据", True, "token 就绪%s" % ("" if pid else " (缺 ProjectId)"))
    if not pid:
        hints.append("补 .edgeone/project.json 的 ProjectId(DescribePagesProjects 按名称 supplykit 查)")

if tok and pid and not NO_NET:
    try:
        body = json.dumps({"Action": "DescribePagesDeployments", "ProjectId": pid,
                           "Offset": 0, "Limit": 1, "OrderBy": "CreatedOn", "Order": "Desc"}).encode()
        req = urllib.request.Request("https://pages-api.cloud.tencent.com/v1", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + tok})
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if r.get("Code") != 0:
            raise RuntimeError(r.get("Message") or "API error")
        d = (r["Data"]["Response"].get("Deployments") or [{}])[0]
        rc, head, _ = run(["git", "rev-parse", "--short", "HEAD"])
        head = head.strip()
        dc = (d.get("RepoCommitHash") or "")[:7]
        same = bool(head) and dc == head
        add("线上部署", True,
            "最近 %s %s · commit %s · Prod=%s · 本地 HEAD=%s%s"
            % (d.get("DeploymentId"), d.get("Status"), dc,
               "yes" if d.get("UsedInProd") else "-", head,
               "" if same else " (不一致)"))
    except Exception as e:
        add("线上部署", False, str(e)[:90], "token 过期/ProjectId 错误 → 重新生成 API Token")
elif not NO_NET:
    add("线上部署", False, "凭据不全, 未查询", None)

# ── 4. push 权限(部署触发器: 本项目为 Git 集成, push 即构建) ──
if not NO_NET:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    rc, out, err = run(["git", "push", "--dry-run", "origin", "HEAD:refs/heads/feat/edgeone"],
                       timeout=25, env=env)
    ok = rc == 0
    _msg = "可推送(部署将随之触发)" if ok else (err or out).split("\n")[-1][:90]
    if "timed out" in _msg or not _msg:
        _msg = "超时(无凭据或网络受限, 无法判定)"
    add("push 权限", ok, _msg,
        "配 GitHub PAT: git remote set-url origin https://<user>:<token>@github.com/Overtrees/Supplykit-edgeone.git")
else:
    add("push 权限", True, "已跳过(--no-net)")

# ── 5. 部署 CLI(可选, 直传项目才用得上) ──
rc, out, _ = run(["edgeone", "--version"], timeout=20)
_ver = next((l.strip() for l in out.splitlines() if l.strip().startswith("version")), "")
add("edgeone CLI", rc == 0, _ver or ("未安装" if rc else "已安装"),
    "npm i -g edgeone(Git 集成项目并非必需, 查部署状态用 scripts/deploy_status.py 即可)")

# ── 输出 ──
w = max(_w(r[0]) for r in rows) + 2
print("\n════════ SupplyKit 环境自检 ════════")
for name, flag, detail in rows:
    print("  %s %s%s%s" % (flag, name, " " * (w - _w(name)), detail))
bad = [r for r in rows if r[1] == BAD]
print("───────────────────────────────────")
print("  %d/%d 通过" % (len(rows) - len(bad), len(rows)))
if hints:
    print("\n  待办:")
    for h in dict.fromkeys(hints):
        print("   · %s" % h)
print()
sys.exit(0 if not bad else 1)
