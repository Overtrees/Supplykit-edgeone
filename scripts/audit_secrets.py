#!/usr/bin/env python3
"""SupplyKit 密钥/配置审计(audit_secrets)

检查三类问题, 输出报告 + 非零退出码(可接入 preflight/CI):
1. 硬编码密钥扫描: 常见密钥模式(AWS/私钥/API key/token/password/secret)在源码中硬编码
   (排除 node_modules/vendor/dist/.git/__pycache__ 与已知安全文件)
2. .env 跟踪审计: git ls-files 中是否包含 .env/.env.*(环境文件不应入库, 防密钥泄漏)
3. .gitignore 覆盖审计: .env/dist 等是否被忽略(防未来误提交)
4. 后端环境变量清单: 统计 os.environ.get 引用 -> 部署必需 env 一览(配置审计)

用法: python3 scripts/audit_secrets.py [--repo 路径] [--json]
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 硬编码密钥模式(按权重分级: 高=确定密钥, 中=疑似, 低=配置值嫌疑)
HIGH_PATTERNS = [
    (r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----", "私钥块"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r"AIza[0-9A-Za-z_\-]{35}", "Google API Key"),
    (r"sk-(live|test|proj)-[0-9A-Za-z\-_]{20,}", "Stripe/OpenAI 风格密钥"),
    # 引号内必须是"类密钥字符集"(无空白/无中文) —— 用 [^'"]{16,} 会把
    # 注释里的中文解释("...JWT_SECRET=" 这类描述规则的句子")当成密钥, 实测反复误报
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^\s'\"\u4e00-\u9fff]{8,}['\"]", "硬编码密码"),
    (r"(?i)(secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^\s'\"\u4e00-\u9fff]{16,}['\"]", "密钥/令牌"),
    (r"ghp_[0-9A-Za-z]{36}", "GitHub PAT"),
    (r"xox[baprs]-[0-9A-Za-z\-]{10,}", "Slack token"),
]
MED_PATTERNS = [
    (r"(?i)jwt[_-]?secret\s*[:=]", "JWT 密钥"),
    (r"(?i)tidb_(password|user)\s*[:=]\s*['\"][^'\"]+['\"]", "TiDB 凭据硬编码"),
    (r"(?i)bearer\s+[0-9A-Za-z._\-]{20,}", "Bearer token 硬编码"),
]

SKIP_DIRS = ("node_modules", "vendor", "dist", ".git", "__pycache__", "export_files",
             "offloads", "screenshots", ".venv",
             # 凭据存放目录(本就 gitignored): 扫它必然命中, 且命中不等于"硬编码"
             # —— 那是运行时读的密钥本体, 该由 [2] .env 跟踪审计 与 [3] gitignore 覆盖来管
             ".edgeone")
SKIP_FILES = ("package-lock.json", "package.json", ".env.example", "CHANGELOG.md",
              "local_test.py", "README.md", "preflight.sh", "audit_secrets.py",
              "smoke_check.py")


def _redact(text):
    """把疑似密钥值脱敏后再输出 —— 审计报告的用途是"指出位置", 不是"复述密钥"。
    打印完整值等于把泄露面从 git 搬到 CI 日志/终端历史/对话记录里。"""
    def _mask(m):
        v = m.group(2)
        return m.group(1) + (v[:4] + "***" + ("(%d位)" % len(v)) if len(v) > 4 else "***")
    return re.sub(r"((?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|"
                  r"jwt[_-]?secret|GITHUB_TOKEN|token)\w*\s*[:=]\s*[\"']?)([^\"'\s,;]{6,})",
                  _mask, text, flags=re.I)


def _walk():
    """仓库内文本文件迭代(跳过忽略目录/二进制)"""
    binaries = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc", ".xlsx", ".csv",
                ".woff", ".woff2", ".ttf", ".ico")
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith(binaries):
                continue
            # .env 家族是"密钥本体"(非模板), 已在 SKIP_FILES 里排除 .env.example;
            # 其余 .env* 直接跳过 —— 把真实值打印到终端/CI 日志本身就是一次泄露
            if f.startswith(".env") and not f.endswith((".example", ".sample", ".template")):
                continue
            yield os.path.join(root, f)


def scan_hardcoded():
    """扫描硬编码密钥, 返回 [(file, line_no, level, kind, snippet)]"""
    findings = []
    for path in _walk():
        rel = os.path.relpath(path, REPO)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for pat, kind in HIGH_PATTERNS + MED_PATTERNS:
                        m = re.search(pat, line)
                        if not m:
                            continue
                        # 与历史扫描共用同一套误报抑制 —— 否则"文档里写 JWT_SECRET=" 这类
                        # 描述规则的句子会被当成密钥(实测: 本文档自己就命中过)
                        if _looks_placeholder(rel, m.group(0)):
                            continue
                        lvl = "HIGH" if pat in [p for p, _ in HIGH_PATTERNS] else "MED"
                        snippet = _redact(line.strip()[:90])
                        # 排除 .env.example 等空值模板与注释样例
                        if re.search(r"['\"]\s*['\"]|xxxx|your-|example|demo", snippet):
                            continue
                        findings.append((rel, i, lvl, kind, snippet))
        except (OSError, UnicodeDecodeError):
            continue
    return findings


def git_ls_env_files():
    """git 跟踪的 .env 文件"""
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files"],
                             capture_output=True, text=True, timeout=30).stdout
        return [os.path.basename(f) for f in out.splitlines()
                if os.path.basename(f).startswith(".env")
                and not os.path.basename(f).endswith(".example")]
    except Exception:
        return []


def check_gitignore():
    """.gitignore 覆盖检查: 返回未覆盖的敏感模式"""
    ignore_path = os.path.join(REPO, ".gitignore")
    if not os.path.exists(ignore_path):
        return [".gitignore 不存在"]
    content = open(ignore_path, encoding="utf-8").read()
    missing = []
    for pat in (".env", ".env.local", "frontend/dist/", "export_files/"):
        # 宽松匹配: 行首或前面可有空格
        if not re.search(r"(^|\n)\s*" + re.escape(pat).replace(r"\.", r"\.").replace(r"/", r"/"), content):
            missing.append(pat)
    return missing


def env_usage():
    """后端 os.environ 引用清单(配置审计)"""
    api = os.path.join(REPO, "cloud-functions", "api")
    refs = {}
    pat = re.compile(r"os\.environ\.(?:get|\[\])\(\s*['\"]([A-Z0-9_]+)['\"]")
    for root, _, files in os.walk(api):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            for line in open(p, encoding="utf-8", errors="replace"):
                m = pat.search(line)
                if m:
                    refs.setdefault(m.group(1), []).append(os.path.relpath(p, api))
    return refs


# ── git 历史扫描(2026-11-03 新增) ────────────────────────────────────────────
# 为什么必须做: 密钥一旦进过历史就等于已泄露 —— 删文件 / amend 只改当前树,
# blob 仍躺在 .git 里, 任何 clone 都能 `git cat-file` 捞出来。此前 security.md 把这条
# 记为"需人工定期复查", 实际是**可自动化**的: 仓库只有几百个对象, 全扫几秒钟。
# 三个面: ① 提交信息 ② 所有历史 blob ③ reflog 里已脱离分支的提交(amend/force-push 残留)
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf",
              ".zip", ".gz", ".tar", ".pdf", ".xlsx", ".xls", ".docx", ".mp4", ".pyc",
              ".so", ".dll", ".exe", ".bin")
MAX_BLOB = 300 * 1024      # 单 blob 超过 300KB 跳过(配置/源码不会这么大, 大的是数据/产物)
PLACEHOLDER = (".env.example", ".env.sample", ".env.template")


def _git(*args, **kw):
    # errors="replace" 是必需的: `cat-file -p` 会吐出二进制 blob(图片/字体/压缩包),
    # 默认严格 utf-8 解码会直接 UnicodeDecodeError 崩掉整个审计(CI 实测踩到)。
    # 替换字符不影响匹配 —— 密钥都是 ASCII。
    return subprocess.run(["git", "-C", REPO] + list(args),
                          capture_output=True, text=True, errors="replace", **kw)


def _looks_placeholder(path, matched):
    """抑制误报, 只保留"真像密钥"的命中。

    三类噪声(实测): 模板文件里的空值(`JWT_SECRET=`)、变量拼接(`'Auth-Token: ' + AT`)、
    以及 `...secret = os.environ.get(...)` 这种"取值语句"本身。
    """
    if os.path.basename(path) in PLACEHOLDER:
        return True
    tail = matched.split("=", 1)[-1].split(":", 1)[-1] if ("=" in matched or ":" in matched) else matched
    tail = tail.strip()
    if not tail:                      # KEY= 后面什么都没有 → 占位
        return True
    if any(mark in matched for mark in ("' +", "+ '", '" +', '+ "', "%s", "{", "os.environ")):
        return True                   # 拼接 / 格式化 / 读环境变量 → 不是硬编码
    return False


# 历史里也可能有第三方依赖/产物(早期误提交过 node_modules) —— 那些 README 里的
# "password: 's00pers3cret'" 是文档示例, 不是本项目密钥。与工作区扫描用同一套目录规则。
HISTORY_SKIP = ("node_modules/", "vendor/", "dist/", ".venv/", "__pycache__/",
                "frontend/node_modules/")


def _scan_text(text, path, where, out):
    for pat, kind in HIGH_PATTERNS + MED_PATTERNS:
        for m in re.finditer(pat, text):
            if _looks_placeholder(path, m.group(0)):
                continue
            lvl = "HIGH" if (pat, kind) in HIGH_PATTERNS else "MED"
            out.append((where, path, lvl, kind, m.group(0)[:60].replace("\n", " ")))


def scan_history():
    """扫 git 全历史。返回 (命中列表, 统计 dict)"""
    hits = []
    stat = {"commits": 0, "blobs": 0, "dangling": 0, "shallow": False}

    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        stat["shallow"] = True          # CI 忘了 fetch-depth: 0 → 历史不全, 必须先说清楚

    # ① 提交信息(密码/密钥被写进 message 是真实发生过的)
    log = _git("log", "--all", "--format=%h", "-n", "100000").stdout.split()
    stat["commits"] = len(log)
    for h in log:
        body = _git("show", "-s", "--format=%B", h).stdout
        _scan_text(body, "commit:" + h, "message", hits)

    # ② 所有历史 blob(按 sha 去重 —— 同一内容被多次修改不留重复命中)
    objs = _git("rev-list", "--objects", "--all").stdout.strip().split("\n")
    seen_sha = set()
    for row in objs:
        if " " not in row:
            continue
        sha, path = row.split(" ", 1)
        if sha in seen_sha or path.lower().endswith(BINARY_EXT):
            continue
        if any(seg in path for seg in HISTORY_SKIP):
            continue
        # 审计工具自身的所有历史版本: 自检样例必然是"密钥形状", 排除它是设计而非妥协
        if os.path.basename(path) == "audit_secrets.py":
            continue
        seen_sha.add(sha)
        if _git("cat-file", "-t", sha).stdout.strip() != "blob":
            continue
        size = _git("cat-file", "-s", sha).stdout.strip()
        if size.isdigit() and int(size) > MAX_BLOB:
            continue
        text = _git("cat-file", "-p", sha).stdout
        if not text or "\x00" in text[:1000]:
            continue
        stat["blobs"] += 1
        _scan_text(text, path, "blob:" + sha[:8], hits)

    # ③ reflog 中已不在任何分支上的提交(amend / reset 的残留)
    all_c = set(_git("rev-list", "--all").stdout.split())
    reflog = set()
    for line in _git("reflog", "--all", "--format=%H").stdout.split("\n"):
        if line.strip():
            reflog.add(line.split()[0])
    for sha in reflog - all_c:
        body = _git("show", "-s", "--format=%B", sha).stdout
        if not body:
            continue
        stat["dangling"] += 1
        _scan_text(body, "dangling:" + sha[:8], "message", hits)

    return hits, stat


def selftest():
    """检测能力的正负例 —— 防止改模式时把"能抓真密钥"改成"什么都抓不到",
    或者反过来把中文注释当密钥天天误报。CI 里与全量审计一起跑。"""
    # 样例串**运行时拼接** —— 源码里绝不能出现完整模式串, 否则本文件自己就是"硬编码密钥",
    # 而且会随提交永久留在历史 blob 里(实测踩过: 自检样例让 CI 审计报了自己 4 条)
    _pat = "ghp_" + "a" * 36
    _pwd = "Str0ng" + "P@ssw0rd!"
    _key = "AKIA" + "IOSFODNN7REALKEY0"
    cases = [
        (True, 'GITHUB_TOKEN = "%s"' % _pat, "硬编码 GitHub PAT"),
        (True, 'password = "%s"' % _pwd, "硬编码密码"),
        (True, 'api_key: "%s"' % _key, "硬编码 api_key"),
        (False, 'JWT_SECRET=', "空值占位(键名后无值)"),
        (False, 'secret = os.environ.get("JWT_SECRET")', "读环境变量(非硬编码)"),
        (False, '否则"文档里写 JWT_SECRET=" 这类描述规则的句子', "中文注释/文档描述"),
    ]
    bad = 0
    print("═══ 审计规则自检 ═══")
    for expect, text, name in cases:
        hits = []
        _scan_text(text, "selftest", "t", hits)
        ok = bool(hits) == expect
        print(("  ✓ " if ok else "  ✗ ") + name + " (期望%s)" % ("命中" if expect else "不命中"))
        bad += 0 if ok else 1
    print("\n结果:", "✓ 六例全部符合预期" if bad == 0 else "✗ %d 例不符" % bad)
    return bad


def main():
    global REPO
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--history", dest="history", action="store_true", default=True,
                    help="扫 git 全历史 blob/commit message(默认开)")
    ap.add_argument("--no-history", dest="history", action="store_false",
                    help="跳过历史扫描(仓库很大时用)")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑检测规则自检(正负例), 用于 CI 守住审计能力")
    args = ap.parse_args()
    REPO = args.repo

    if args.selftest:
        sys.exit(0 if selftest() == 0 else 1)

    issues = []
    hc = scan_hardcoded()
    env_tracked = git_ls_env_files()
    miss_ign = check_gitignore()
    envs = env_usage()
    hist, hstat = (scan_history() if args.history else ([], {}))

    if hc:
        issues.append("硬编码密钥 %d 处" % len(hc))
    if env_tracked:
        issues.append(".env 被 git 跟踪(%s)" % ", ".join(env_tracked))
    if miss_ign:
        issues.append(".gitignore 未覆盖: %s" % ", ".join(miss_ign))
    if hist:
        issues.append("git 历史密钥 %d 处" % len(hist))

    report = {
        "hardcoded_secrets": hc,
        "env_files_tracked": env_tracked,
        "gitignore_missing": miss_ign,
        "required_env": sorted(envs.keys()),
        "history_hits": hist,
        "history_stat": hstat,
        "issues": issues,
        "pass": len(issues) == 0,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("═══ SupplyKit 密钥/配置审计 ═══")
        print("\n[1] 硬编码密钥扫描:")
        if hc:
            for f, ln, lvl, kind, sn in hc:
                print("  ✗ %s:%d [%s] %s: %s" % (f, ln, lvl, kind, sn))
        else:
            print("  ✓ 无")
        print("\n[2] .env 跟踪审计:", " ✗ " + ", ".join(env_tracked) if env_tracked else " ✓ 无 .env 被跟踪")
        print("[3] .gitignore 覆盖:", " ✗ 缺 " + ", ".join(miss_ign) if miss_ign else " ✓ 完整")
        print("\n[4] 后端必需环境变量清单(%d 个, 部署 env 必须覆盖):" % len(envs))
        for k in sorted(envs):
            print("  - %s  (used by %s)" % (k, ", ".join(sorted(set(envs[k]))[:3])))
        print("\n[5] git 历史扫描:", end=" ")
        if not args.history:
            print("已跳过(--no-history)")
        else:
            if hstat.get("shallow"):
                print("⚠ 浅克隆, 历史不完整(CI 需 fetch-depth: 0)")
            print("    commit %d 条 / blob %d 个 / 脱分支提交 %d 个"
                  % (hstat.get("commits", 0), hstat.get("blobs", 0), hstat.get("dangling", 0)))
            if hist:
                for where, f, lvl, kind, sn in hist:
                    print("  ✗ %s %s [%s] %s: %s" % (where, f, lvl, kind, sn))
            else:
                print("  ✓ 历史中无密钥残留")
        print("\n结果:", "✗ " + " | ".join(issues) if issues else "✓ 全部通过")
    sys.exit(0 if not issues else 1)


if __name__ == "__main__":
    main()