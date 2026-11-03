#!/usr/bin/env python3
"""把 EdgeOne 项目凭据写进 GitHub Secrets(本地一次性工具, 幂等)

背景: 仓库既配了 GitHub Secrets, 又在 EdgeOne 项目里配了一套, 两套各改各的,
必然漂移(2026-11-03 CI 连错库就是这么来的)。改动后 CI 不再依赖 TIDB_* 副本,
只保留一个 EdgeOne API Token —— 于是它必须由人手动放对一次, 这个脚本就是那一步。

写什么: 只有 EDGEONE_PAGES_API_TOKEN(取自 .edgeone/auth.json)。
         GitHub Secrets 是只写的, 写完读不回来, 所以本脚本只能"确保已写入",
         不能"校验值是否正确"。真正的校验在下一次 CI: 日志里会打
         `凭据来源=edgeone` 还是 `secrets`。

用法:
    python3 scripts/sync_ci_secret.py            # 写入/覆盖
    python3 scripts/sync_ci_secret.py --dry-run  # 只检查权限与公钥, 不写
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

API = "https://api.github.com"
ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
DRY = "--dry-run" in sys.argv
NAME = "EDGEONE_PAGES_API_TOKEN"


def cfg():
    own, repo = "Overtrees", "Supplykit-edgeone"
    p = os.path.join(ROOT, ".edgeone", "github.json")
    if os.path.exists(p):
        c = json.load(open(p, encoding="utf-8"))
        own, repo = c.get("owner", own), c.get("repo", repo)
    return own, repo


def gh_tok():
    t = os.environ.get("GITHUB_TOKEN")
    if not t:
        t = json.load(open(os.path.join(ROOT, ".edgeone", "auth.json"), encoding="utf-8"))["GITHUB_TOKEN"]
    return t


def call(method, path, payload=None, tok=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=body, method=method, headers={
        "Authorization": "Bearer " + (tok or gh_tok()),
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "supplykit-ci-secret"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def main():
    from nacl import encoding, public
    own, repo = cfg()
    val = json.load(open(os.path.join(ROOT, ".edgeone", "auth.json"),
                         encoding="utf-8"))[NAME]
    pk = call("GET", "/repos/%s/%s/actions/secrets/public-key" % (own, repo))
    print("仓库 %s/%s 公钥 key_id=%s" % (own, repo, pk.get("key_id")))
    if DRY:
        print("dry-run: 不写入(已确认有权限拿到公钥, 说明 token 具备 repo scope)")
        return 0
    sealed = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder))
    enc = base64.b64encode(sealed.encrypt(val.encode())).decode()
    call("PUT", "/repos/%s/%s/actions/secrets/%s" % (own, repo, NAME),
         {"encrypted_value": enc, "key_id": pk["key_id"]})
    print("已写入 secret %s (值只有 %d 字节, 不回显)" % (NAME, len(val)))
    print("下一步: 推送 → CI 日志应出现「数据库连接来源: edgeone」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
