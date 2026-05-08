"""
WeGame Rocom 扫码登录脚本
=======================
通过 QQ / 微信扫码获取 frameworkToken，用于后续游戏数据查询。

用法：
    python login.py              # QQ 扫码登录
    python login.py --wechat     # 微信扫码登录
    python login.py --show       # 查看当前保存的凭证信息

流程：
    1. 调用 API 获取二维码（base64 图片）
    2. 弹出二维码图片供用户扫描
    3. 轮询扫码状态，等待用户确认
    4. 获取 token 并保存到 token.json
"""

import os
import sys
import json
import base64
import asyncio
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
TOKEN_PATH = SCRIPT_DIR / "token.json"

CN_TZ = timezone(timedelta(hours=8))


def load_config():
    """加载配置文件。"""
    if not CONFIG_PATH.exists():
        print(f"[错误] 配置文件不存在: {CONFIG_PATH}")
        print("请先创建 config.yaml，至少填写 api_key")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_token(data: dict):
    """将登录凭证保存到本地文件。"""
    payload = {
        "saved_at": datetime.now(CN_TZ).isoformat(),
        **data,
    }
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n[成功] 凭证已保存到 {TOKEN_PATH}")


def load_token():
    """读取本地保存的凭证。"""
    if not TOKEN_PATH.exists():
        return None
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def show_token():
    """展示当前保存的凭证信息。"""
    token = load_token()
    if not token:
        print("当前没有保存的登录凭证。")
        return
    print("========== 当前保存的凭证 ==========")
    for k, v in token.items():
        if k in ("qr_image",):
            print(f"  {k}: <base64 图片, 已省略>")
        else:
            print(f"  {k}: {v}")
    print("====================================")


def _fix_base64(s: str) -> str:
    """修正 base64 字符串的 padding 并去除 data URL 前缀。"""
    s = s.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[1]
    missing = len(s) % 4
    if missing:
        s += "=" * (4 - missing)
    return s


def display_qr_terminal(qr_base64: str):
    """在终端用 ASCII 灰度图展示二维码图片。"""
    try:
        from io import BytesIO
        from PIL import Image

        img_data = base64.b64decode(_fix_base64(qr_base64))
        pil_img = Image.open(BytesIO(img_data)).convert("L")
        pil_img = pil_img.resize((60, 60))
        pixels = pil_img.load()
        w, h = pil_img.size
        chars = " ░▒▓█"
        for y in range(0, h, 2):
            line = ""
            for x in range(w):
                brightness = pixels[x, y]
                idx = min(brightness * len(chars) // 256, len(chars) - 1)
                line += chars[idx] * 2
            print(line)
    except ImportError:
        print("[提示] 未安装 Pillow，无法在终端显示二维码")


def save_qr_image(qr_base64: str) -> str:
    """将 base64 二维码保存为 PNG 文件并返回路径。"""
    img_data = base64.b64decode(_fix_base64(qr_base64))
    qr_path = SCRIPT_DIR / "qrcode.png"
    with open(qr_path, "wb") as f:
        f.write(img_data)
    return str(qr_path)


async def qq_qr_login(config: dict) -> dict | None:
    """QQ 扫码登录流程。"""
    cfg = config["api"]
    base_url = cfg["base_url"].rstrip("/")
    api_key = cfg["api_key"]
    login_cfg = config.get("login", {})

    headers = {"X-API-Key": api_key}
    params = {
        "client_type": login_cfg.get("client_type", "bot"),
        "client_id": login_cfg.get("client_id", "astrbot"),
        "provider": login_cfg.get("provider", "rocom"),
    }
    uid = login_cfg.get("user_identifier", "")
    if uid:
        params["user_identifier"] = uid

    async with httpx.AsyncClient(timeout=cfg.get("timeout", 15)) as client:
        # ── 1. 获取二维码 ──
        print("[1/4] 正在请求 QQ 登录二维码...")
        resp = await client.get(
            f"{base_url}/api/v1/login/wegame/qr",
            headers=headers,
            params=params,
        )
        if resp.status_code != 200:
            print(f"[错误] 获取二维码失败: HTTP {resp.status_code} {resp.text[:200]}")
            return None
        data = resp.json()
        if data.get("code") != 0:
            print(f"[错误] API 返回错误: {data.get('message', '未知')}")
            return None
        payload = data.get("data", {})
        fw_token = payload.get("frameworkToken") or payload.get("framework_token")
        qr_image = payload.get("qrImage") or payload.get("qr_image")
        if not fw_token or not qr_image:
            print(f"[错误] 响应缺少 frameworkToken 或 qrImage: {json.dumps(payload, ensure_ascii=False)[:300]}")
            return None

        print(f"[成功] 获取到 frameworkToken: {fw_token[:20]}...")

        # ── 2. 展示二维码 ──
        print("[2/4] 正在生成二维码图片...")
        qr_path = save_qr_image(qr_image)
        print(f"二维码已保存到: {qr_path}")
        print("请打开该图片，使用 QQ 扫描。")
        try:
            os.startfile(qr_path)
        except Exception:
            pass

        # 也尝试终端展示
        display_qr_terminal(qr_image)

        # ── 3. 轮询扫码状态 ──
        print("[3/4] 等待扫码...")
        poll_headers = {"X-API-Key": api_key, "X-Framework-Token": fw_token}
        poll_params = {}
        if uid:
            poll_params["user_identifier"] = uid

        for attempt in range(120):  # 最多等 2 分钟
            await asyncio.sleep(2)
            try:
                resp = await client.get(
                    f"{base_url}/api/v1/login/wegame/status",
                    headers=poll_headers,
                    params=poll_params,
                )
            except Exception as e:
                print(f"  [警告] 轮询失败 (第 {attempt + 1} 次): {e}")
                continue

            if resp.status_code != 200:
                continue
            sdata = resp.json()
            if sdata.get("code") != 0:
                continue
            spayload = sdata.get("data", {})
            status = spayload.get("status", "")
            print(f"  [{attempt + 1}] 状态: {status}")
            if status == "scanned":
                print("[提示] 二维码已扫描，请在手机上确认登录...")
            if status in ("confirmed", "done"):
                print("[成功] 用户已确认登录！")
                break
        else:
            print("[错误] 等待扫码超时，请重试。")
            return None

        # ── 4. 获取 token ──
        print("[4/4] 正在获取登录凭证...")
        token_params = {}
        if uid:
            token_params["user_identifier"] = uid
        resp = await client.get(
            f"{base_url}/api/v1/login/wegame/token",
            headers=poll_headers,
            params=token_params,
        )
        if resp.status_code != 200:
            print(f"[错误] 获取 token 失败: HTTP {resp.status_code}")
            return None
        tdata = resp.json()
        if tdata.get("code") != 0:
            print(f"[错误] 获取 token 返回错误: {tdata.get('message', '未知')}")
            return None
        token_payload = tdata.get("data", {})

        result = {
            "provider": login_cfg.get("provider", "rocom"),
            "framework_token": fw_token,
            "tgp_id": token_payload.get("tgp_id", ""),
            "tgp_ticket": token_payload.get("tgp_ticket", ""),
            "role_id": token_payload.get("role_id", ""),
            "nickname": token_payload.get("nickname", ""),
        }
        return result


async def wechat_qr_login(config: dict) -> dict | None:
    """微信扫码登录流程。"""
    cfg = config["api"]
    base_url = cfg["base_url"].rstrip("/")
    api_key = cfg["api_key"]
    login_cfg = config.get("login", {})

    headers = {"X-API-Key": api_key}
    params = {
        "client_type": login_cfg.get("client_type", "bot"),
        "client_id": login_cfg.get("client_id", "astrbot"),
        "provider": login_cfg.get("provider", "rocom"),
    }
    uid = login_cfg.get("user_identifier", "")
    if uid:
        params["user_identifier"] = uid

    async with httpx.AsyncClient(timeout=cfg.get("timeout", 15)) as client:
        # ── 1. 获取二维码 ──
        print("[1/4] 正在请求微信登录二维码...")
        resp = await client.get(
            f"{base_url}/api/v1/login/wegame/wechat/qr",
            headers=headers,
            params=params,
        )
        if resp.status_code != 200:
            print(f"[错误] 获取二维码失败: HTTP {resp.status_code} {resp.text[:200]}")
            return None
        data = resp.json()
        if data.get("code") != 0:
            print(f"[错误] API 返回错误: {data.get('message', '未知')}")
            return None
        payload = data.get("data", {})
        fw_token = payload.get("frameworkToken") or payload.get("framework_token")
        qr_url = payload.get("qrImage") or payload.get("qr_image") or payload.get("qr_url")

        if not fw_token:
            print(f"[错误] 响应缺少 frameworkToken: {json.dumps(payload, ensure_ascii=False)[:300]}")
            return None

        print(f"[成功] 获取到 frameworkToken: {fw_token[:20]}...")

        # ── 2. 展示二维码 ──
        print(f"[2/4] 请使用微信扫描以下链接中的二维码：")
        print(f"  {qr_url}")
        if qr_url:
            # 尝试用浏览器打开
            try:
                import webbrowser
                webbrowser.open(qr_url)
            except Exception:
                pass

        # ── 3. 轮询扫码状态 ──
        print("[3/4] 等待扫码...")
        poll_headers = {"X-API-Key": api_key, "X-Framework-Token": fw_token}
        poll_params = {}
        if uid:
            poll_params["user_identifier"] = uid

        for attempt in range(120):
            await asyncio.sleep(2)
            try:
                resp = await client.get(
                    f"{base_url}/api/v1/login/wegame/wechat/status",
                    headers=poll_headers,
                    params=poll_params,
                )
            except Exception as e:
                print(f"  [警告] 轮询失败 (第 {attempt + 1} 次): {e}")
                continue

            if resp.status_code != 200:
                continue
            sdata = resp.json()
            if sdata.get("code") != 0:
                continue
            spayload = sdata.get("data", {})
            status = spayload.get("status", "")
            print(f"  [{attempt + 1}] 状态: {status}")
            if status == "scanned":
                print("[提示] 二维码已扫描，请在手机上确认登录...")
            if status in ("confirmed", "done"):
                print("[成功] 用户已确认登录！")
                break
        else:
            print("[错误] 等待扫码超时，请重试。")
            return None

        # ── 4. 获取 token ──
        print("[4/4] 正在获取登录凭证...")
        token_params = {}
        if uid:
            token_params["user_identifier"] = uid
        resp = await client.get(
            f"{base_url}/api/v1/login/wegame/wechat/token",
            headers=poll_headers,
            params=token_params,
        )
        if resp.status_code != 200:
            print(f"[错误] 获取 token 失败: HTTP {resp.status_code}")
            return None
        tdata = resp.json()
        if tdata.get("code") != 0:
            print(f"[错误] 获取 token 返回错误: {tdata.get('message', '未知')}")
            return None
        token_payload = tdata.get("data", {})

        result = {
            "provider": login_cfg.get("provider", "rocom"),
            "framework_token": fw_token,
            "tgp_id": token_payload.get("tgp_id", ""),
            "tgp_ticket": token_payload.get("tgp_ticket", ""),
            "role_id": token_payload.get("role_id", ""),
            "nickname": token_payload.get("nickname", ""),
        }
        return result


async def main():
    parser = argparse.ArgumentParser(description="WeGame Rocom 扫码登录")
    parser.add_argument("--wechat", action="store_true", help="使用微信扫码登录（默认 QQ）")
    parser.add_argument("--show", action="store_true", help="查看当前保存的凭证信息")
    args = parser.parse_args()

    if args.show:
        show_token()
        return

    config = load_config()
    api_key = config.get("api", {}).get("api_key", "")
    if not api_key:
        print("[错误] 请在 config.yaml 中填写 api_key")
        sys.exit(1)

    if args.wechat:
        result = await wechat_qr_login(config)
    else:
        result = await qq_qr_login(config)

    if result:
        save_token(result)
        print(f"\n========== 登录成功 ==========")
        print(f"  昵称: {result.get('nickname', '未知')}")
        print(f"  TGP ID: {result.get('tgp_id', '未知')}")
        print(f"  Role ID: {result.get('role_id', '未知')}")
        print(f"  Framework Token: {result['framework_token'][:30]}...")
        print(f"==============================")


if __name__ == "__main__":
    asyncio.run(main())
