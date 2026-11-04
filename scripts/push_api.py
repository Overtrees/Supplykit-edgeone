#!/usr/bin/env python3
"""经 GitHub Git Data API 推送(替代 git push)

为什么要它: 本沙箱只有 api.github.com 可达, github.com:443 的 TLS 会在
131s 后断开(clone/fetch/push 全失败), 但 API 稳定。于是把"提交→传 blob→建 tree
→建 commit→移 ref"这套 git 协议手工走一遍。

代价(必须知道):
- 本地 commit 的 SHA 与远端**不同**(API 建的提交 author/committer 时间戳不同),
  所以 `deploy_status.py` 的 "Commit == 本地 HEAD" 会显示不一致 —— 这是预期,
  真正要比的是 **远端 HEAD 与本次推送内容是否一致**(脚本结束会打印对照)。
- 一次推送只产**一个提交**: 内容 = 本地 HEAD 全树与远端 HEAD 全树的差异(按 blob 内容算)。
  本地历史再漂亮, 远端也只看到一个 squash 提交。适合"单机接管、按批交付"。

用法:
    python3 scripts/push_api.py -m "fix(dash): ..."          # 同步并提交
    python3 scripts/push_api.py -m "..." --dry-run           # 只列出差异文件

凭据: .edgeone/auth.json 的 GITHUB_TOKEN, 或环境变量 GITHUB_TOKEN
      (与 EDGEONE_PAGES_API_TOKEN 不同, 需要 repo contents:write)
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "https://api.github.com"
ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


def _cfg():
    own, repo, br = "Overtrees", "Supplykit-edgeone", "feat/edgeone"
    tok = os.environ.get("GITHUB_TOKEN")
    auth = os.path.join(ROOT, ".edgeone", "auth.json")
    proj = os.path.join(ROOT, ".edgeone", "github.json")
    if not tok and os.path.exists(auth):
        tok = json.load(open(auth)).get("GITHUB_TOKEN")
    if os.path.exists(proj):
        c = json.load(open(proj))
        own, repo, br = c.get("owner", own), c.get("repo", repo), c.get("branch", br)
    if not tok:
        sys.exit("缺 GITHUB_TOKEN: 写入 .edgeone/auth.json 或设环境变量")
    return tok, own, repo, br


TOK, OWN, REPO, BR = _cfg()


def call(method, path, payload=None, tries=6):
    for i in range(tries):
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(API + path, data=body, method=method,
                                         headers={"Authorization": "Bearer " + TOK,
                                                  "Accept": "application/vnd.github+json",
                                                  "Content-Type": "application/json",
                                                  "User-Agent": "supplykit-push"})
            # 2026-11-04: 大 tree(~800KB) 在 30s 超时/代理链路上常 IncompleteRead,
            # 超时放宽到 120s, 重试间隔指数退避(2/4/8/16/32)
            return json.loads(urllib.request.urlopen(req, timeout=120).read() or b"{}")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (2 ** i))


def main():
    if "-h" in sys.argv or "--help" in sys.argv or ("-m" not in sys.argv and "--dry-run" not in sys.argv):
        print(__doc__)
        return 0
    msg = sys.argv[sys.argv.index("-m") + 1] if "-m" in sys.argv else "(dry-run)"
    dry = "--dry-run" in sys.argv

    head = call("GET", "/repos/%s/%s/commits/%s" % (OWN, REPO, BR))
    base_sha, base_tree = head["sha"], head["commit"]["tree"]["sha"]
    remote = {e["path"]: (e["mode"], e["sha"]) for e in
              call("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWN, REPO, base_tree))["tree"]
              if e["type"] == "blob"}
    local = {}
    # -z: NUL 分隔且**不做 quotepath 转义**。不加 -z 时中文文件名会输出成
    #   "docs/decisions/0002-\350\264\246..." 这种八进制转义串, 与远端 API 返回的
    #   真实 UTF-8 路径比不出来 —— 同一个文件会被误判成"新增一个 + 删除一个",
    #   推上去就在仓库里留一个乱码名文件(实测踩过)。
    raw = subprocess.check_output(["git", "ls-tree", "-r", "-z", "HEAD"], text=True)
    for line in raw.split("\x00"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, _t, sha = meta.split()
        local[path] = (mode, sha)

    add = [p for p in local if p not in remote]
    mod = [p for p in local if p in remote and local[p] != remote[p]]
    dele = [p for p in remote if p not in local]
    print("远端 HEAD %s · 新增 %d / 修改 %d / 删除 %d" % (base_sha[:7], len(add), len(mod), len(dele)))
    for p in sorted(add + mod + dele)[:40]:
        print("  %s %s" % ("+" if p in add else ("-" if p in dele else "M"), p))
    if dry:
        return 0
    if not (add or mod or dele):
        print("无差异, 不产生提交")
        return 0

    entries = []
    for p in add + mod:
        content = subprocess.check_output(["git", "show", "HEAD:" + p])
        blob = call("POST", "/repos/%s/%s/git/blobs" % (OWN, REPO),
                    {"content": base64.b64encode(content).decode(), "encoding": "base64"})
        # 模式必须照抄本地(git 只认 100644/100755)——否则可执行位在远端被吃掉(preflight.sh 曾中招)
        entries.append({"path": p, "mode": local[p][0], "type": "blob", "sha": blob["sha"]})
    for p in dele:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    tree = call("POST", "/repos/%s/%s/git/trees" % (OWN, REPO), {"base_tree": base_tree, "tree": entries})
    nc = call("POST", "/repos/%s/%s/git/commits" % (OWN, REPO),
              {"message": msg, "tree": tree["sha"], "parents": [base_sha],
               "author": {"name": "WorkBuddy", "email": "workbuddy@local"}})
    call("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (OWN, REPO, BR), {"sha": nc["sha"]})

    # 复核: 远端新树必须与本地 HEAD 树逐文件一致(否则说明漏传/模式错)
    now = call("GET", "/repos/%s/%s/commits/%s" % (OWN, REPO, BR))
    rt = {e["path"]: (e["mode"], e["sha"]) for e in
          call("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWN, REPO, now["commit"]["tree"]["sha"]))["tree"]
          if e["type"] == "blob"}
    bad = [p for p in local if rt.get(p) != local[p]] + [p for p in rt if p not in local]
    print("远端 HEAD 已更新: %s" % now["sha"][:7])
    print("逐文件复核: %s" % ("一致" if not bad else "不一致 %s" % bad[:5]))
    json.dump({"github_head": now["sha"]}, open(os.path.join(ROOT, ".edgeone", "last_push.json"), "w"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
