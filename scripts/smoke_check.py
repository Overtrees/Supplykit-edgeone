#!/usr/bin/env python3
"""SupplyKit 部署冒烟检查(smoke_check) —— 构建/部署失败通知链路核心

背景: Makers 部署"Success"但函数路由丢失(health 变 SPA HTML)/前端空白页曾多次发生,
      部署后必须冒烟验证。本脚本:
1. GET /api/health → 必须为 JSON 且 status=ok(函数路由存活, 非 SPA HTML)
2. GET / → 必须 200 且 HTML 含 id="root"(前端可加载) 
3. GET /api/dashboard/summary?channel=jd → 带 token 时验证业务接口(可选 --token)
4. 任一失败: 输出 FAIL + 可选 webhook 通知(钉钉/企微 msgtype=text) + 退出码 1

用法:
  python3 scripts/smoke_check.py [--url https://supplykit.top] [--token <JWT>]
      [--notify-webhook <url>] [--timeout 30]
退出码: 0=线上正常  1=冒烟失败(部署未就绪/构建失败/路由丢失)
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error


def _get(url, timeout, token=""):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("User-Agent", "supplykit-smoke/1.0")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, body, time.time() - t0


def _notify(webhook, title, lines):
    """钉钉/企微兼容 text 消息; 失败不阻塞主流程"""
    if not webhook:
        return
    try:
        text = "\n".join(["【SupplyKit 冒烟失败】%s" % title] + lines)
        data = json.dumps({"msgtype": "text", "text": {"content": text}}).encode()
        req = urllib.request.Request(webhook, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        print("  [notify] webhook 通知已发送")
    except Exception as e:
        print("  [notify] webhook 发送失败: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://supplykit.top")
    ap.add_argument("--token", default="", help="JWT(可选, 验证鉴权业务接口)")
    ap.add_argument("--notify-webhook", default="", help="失败时通知 webhook(钉钉/企微)")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    fails = []

    print("═══ SupplyKit 部署冒烟: %s ═══" % base)
    time.sleep(1)  # 构建传播延迟

    # 1. /api/health —— 函数路由存活判定(JSON vs SPA HTML)
    try:
        st, body, el = _get(base + "/api/health", args.timeout)
        text = body.decode("utf-8", errors="replace").strip()
        if st == 200 and text.startswith("{") and '"ok"' in text:
            try:
                d = json.loads(text)
                db = d.get("db", "?")
                ver = d.get("version", "?")
                print("  ✓ health: HTTP 200 JSON (%s, db=%s, version=%s) %.1fs"
                      % (d.get("status", "?"), db, ver, el))
                if d.get("status") != "ok":
                    fails.append("health status=%s (db=%s)" % (d.get("status"), db))
            except Exception as e:
                fails.append("health JSON 解析失败: %s" % e)
        else:
            fails.append("health 非函数响应(%s, 前80字符: %s)" % (st, text[:80]))
    except Exception as e:
        fails.append("health 请求失败: %s" % e)

    # 2. 前端主页 —— #root 存在(React 挂载点)= 前端资源可配
    try:
        st, body, el = _get(base + "/", args.timeout)
        text = body.decode("utf-8", errors="replace")
        if st == 200 and 'id="root"' in text:
            print("  ✓ 前端: HTTP 200, #root 存在 %.1fs" % el)
        else:
            fails.append("前端异常(st=%s, root=%s)" % (st, 'id="root"' in text))
    except Exception as e:
        fails.append("前端请求失败: %s" % e)

    # 3. 鉴权业务接口(有 token 时)
    if args.token:
        try:
            st, body, el = _get(base + "/api/dashboard/summary?channel=jd", args.timeout, args.token)
            text = body.decode("utf-8", errors="replace")
            if st == 200 and '"ok":true' in text and '"summary"' in text:
                print("  ✓ summary: 鉴权接口正常 %.1fs" % el)
            else:
                fails.append("summary 异常(st=%s, %s)" % (st, text[:80]))
        except Exception as e:
            fails.append("summary 请求失败: %s" % e)

    print("═══ " + ("✗ 冒烟失败: " + "; ".join(fails) if fails else "✓ 冒烟全部通过") + " ═══")
    if fails:
        _notify(args.notify_webhook, base, fails)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())