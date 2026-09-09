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
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "硬编码密码"),
    (r"(?i)(secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^'\"]{16,}['\"]", "密钥/令牌"),
    (r"ghp_[0-9A-Za-z]{36}", "GitHub PAT"),
    (r"xox[baprs]-[0-9A-Za-z\-]{10,}", "Slack token"),
]
MED_PATTERNS = [
    (r"(?i)jwt[_-]?secret\s*[:=]", "JWT 密钥"),
    (r"(?i)tidb_(password|user)\s*[:=]\s*['\"][^'\"]+['\"]", "TiDB 凭据硬编码"),
    (r"(?i)bearer\s+[0-9A-Za-z._\-]{20,}", "Bearer token 硬编码"),
]

SKIP_DIRS = ("node_modules", "vendor", "dist", ".git", "__pycache__", "export_files",
             "offloads", "screenshots", ".venv")
SKIP_FILES = ("package-lock.json", "package.json", ".env.example", "CHANGELOG.md",
              "local_test.py", "README.md", "preflight.sh", "audit_secrets.py",
              "smoke_check.py")


def _walk():
    """仓库内文本文件迭代(跳过忽略目录/二进制)"""
    binaries = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc", ".xlsx", ".csv",
                ".woff", ".woff2", ".ttf", ".ico")
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith(binaries):
                continue
            yield os.path.join(root, f)


def scan_hardcoded():
    """扫描硬编码密钥, 返回 [(file, line_no, level, kind, snippet)]"""
    findings = []
    for path in _walk():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for pat, kind in HIGH_PATTERNS + MED_PATTERNS:
                        if re.search(pat, line):
                            lvl = "HIGH" if pat in [p for p, _ in HIGH_PATTERNS] else "MED"
                            snippet = line.strip()[:90]
                            # 排除 .env.example 等空值模板与注释样例
                            if re.search(r"['\"]\s*['\"]|xxxx|your-|example|demo", snippet):
                                continue
                            findings.append((os.path.relpath(path, REPO), i, lvl, kind, snippet))
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


def main():
    global REPO
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()
    REPO = args.repo

    issues = []
    hc = scan_hardcoded()
    env_tracked = git_ls_env_files()
    miss_ign = check_gitignore()
    envs = env_usage()

    if hc:
        issues.append("硬编码密钥 %d 处" % len(hc))
    if env_tracked:
        issues.append(".env 被 git 跟踪(%s)" % ", ".join(env_tracked))
    if miss_ign:
        issues.append(".gitignore 未覆盖: %s" % ", ".join(miss_ign))

    report = {
        "hardcoded_secrets": hc,
        "env_files_tracked": env_tracked,
        "gitignore_missing": miss_ign,
        "required_env": sorted(envs.keys()),
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
        print("\n结果:", "✗ " + " | ".join(issues) if issues else "✓ 全部通过")
    sys.exit(0 if not issues else 1)


if __name__ == "__main__":
    main()