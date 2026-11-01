#!/usr/bin/env python3
"""查询 EdgeOne Makers 部署状态(只读)

背景: CLI 只有 deploy 命令, 没有 status 子命令。平台 API 的
DescribePagesDeployments 未在 CLI 暴露, 但可直连调用 —— 部署是否完成、
跑的是哪个 commit、有没有上生产, 一次请求就能看到, 不必靠"改一行代码再调
业务接口猜"。

用法:
    python3 scripts/deploy_status.py            # 最近 3 次
    python3 scripts/deploy_status.py 10         # 最近 10 次

判读:
    Status=Success 且 Prod=yes 且 Commit == 本地 HEAD → 线上就是你的代码
    参考耗时: 推送到部署完成约 2~2.5 分钟(实测 03:59:50 → 04:02:14)

依赖: .edgeone/auth.json 的 EDGEONE_PAGES_API_TOKEN、.edgeone/project.json
china 区 endpoint 为 pages-api.cloud.tencent.com/v1(global 区为 pages-api.edgeone.ai/v1)
"""
import json
import subprocess
import sys
import urllib.request

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()

tok = json.load(open(ROOT + "/.edgeone/auth.json"))["EDGEONE_PAGES_API_TOKEN"]
pid = json.load(open(ROOT + "/.edgeone/project.json"))["ProjectId"]

body = json.dumps({"Action": "DescribePagesDeployments", "ProjectId": pid,
                   "Offset": 0, "Limit": N, "OrderBy": "CreatedOn", "Order": "Desc"}).encode()
req = urllib.request.Request(
    "https://pages-api.cloud.tencent.com/v1", data=body,
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})

resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
if resp.get("Code") != 0:
    print("API error:", resp)
    raise SystemExit(1)

r = resp["Data"]["Response"]
local = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
print("TotalCount=%s  本地 HEAD=%s" % (r.get("TotalCount"), local[:7]))
print("%-14s %-8s %-10s %-19s %-19s %s" % ("DeploymentId", "Status", "Commit",
                                           "CreatedOn(UTC)", "ModifiedOn(UTC)", "Prod"))
for d in r.get("Deployments", []):
    print("%-14s %-8s %-10s %-19s %-19s %s" % (
        d.get("DeploymentId"), d.get("Status"), (d.get("RepoCommitHash") or "")[:7],
        (d.get("CreatedOn") or "")[:19], (d.get("ModifiedOn") or "")[:19],
        "yes" if d.get("UsedInProd") else "-"))
