"""
远行商人监控脚本
==============
每天 8:01 / 12:01 / 16:01 / 20:01 自动拉取远行商人商品，
有上新时通过 QQ 邮箱发送通知。

用法：
    python merchant.py                  # 前台持续运行（定时轮询 + 邮件通知）
    python merchant.py --once           # 只检查一次并发送邮件，然后退出
    python merchant.py --refresh        # 只查询并显示当前商品（不发送邮件）
    python merchant.py --json           # 输出原始 JSON 数据
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

CN_TZ = timezone(timedelta(hours=8))

# 每日检查时间点（北京时间）
CHECK_HOURS = [8, 12, 16, 20]
CHECK_MINUTE = 1

# ── 终端颜色 ──
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"


def load_config():
    if not CONFIG_PATH.exists():
        print(f"[错误] 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════
#  时间 / 轮次计算
# ══════════════════════════════════════════════════════

def format_countdown(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h and m:
        return f"{h}小时{m}分钟"
    if h:
        return f"{h}小时"
    return f"{m}分钟"


def format_timestamp_ms(ts_ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=CN_TZ)
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "--"


def format_time_window(start_ms, end_ms) -> str:
    if start_ms is None or end_ms is None:
        return "当前轮次"
    s = format_timestamp_ms(start_ms)
    e = format_timestamp_ms(end_ms)
    if s == "--" or e == "--":
        return "当前轮次"
    if s[:5] == e[:5]:
        return f"{s} - {e[6:]}"
    return f"{s} - {e}"


def current_merchant_round(now: datetime | None = None) -> dict:
    now = now or datetime.now(CN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    round_index = None
    round_end = None
    if start <= now < start + timedelta(hours=16):
        delta_seconds = int((now - start).total_seconds())
        round_index = delta_seconds // int(timedelta(hours=4).total_seconds()) + 1
        round_start = start + timedelta(hours=4 * (round_index - 1))
        round_end = round_start + timedelta(hours=4)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "current": round_index,
        "total": 4,
        "round_id": (
            f"{now.strftime('%Y-%m-%d')}-{round_index}"
            if round_index
            else f"{now.strftime('%Y-%m-%d')}-closed"
        ),
        "is_open": round_index is not None,
        "countdown": format_countdown(round_end - now) if round_end else "未开市",
    }


# ══════════════════════════════════════════════════════
#  API 调用 & 数据解析
# ══════════════════════════════════════════════════════

async def fetch_merchant_info(config: dict, refresh: bool = False) -> dict | None:
    cfg = config["api"]
    base_url = cfg["base_url"].rstrip("/")
    api_key = cfg["api_key"]
    timeout = cfg.get("timeout", 15)
    headers = {"X-API-Key": api_key}
    params = {"refresh": "true" if refresh else "false"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{base_url}/api/v1/games/rocom/merchant/info",
                headers=headers,
                params=params,
            )
        except httpx.TimeoutException:
            print(f"[错误] 请求超时（{timeout}s）")
            _send_alert(config, "请求超时", f"API 请求超时（{timeout}s）")
            return None
        except httpx.RequestError as e:
            print(f"[错误] 请求失败: {e}")
            _send_alert(config, "请求失败", str(e))
            return None

        if resp.status_code != 200:
            print(f"[错误] HTTP {resp.status_code}: {resp.text[:200]}")
            _send_alert(config, f"HTTP {resp.status_code}", resp.text[:200])
            return None

        try:
            data = resp.json()
        except Exception as e:
            print(f"[错误] JSON 解析失败: {e}")
            _send_alert(config, "JSON解析失败", str(e))
            return None

        if data.get("code") != 0:
            print(f"[错误] API 返回错误: {data.get('message', '未知')}")
            _send_alert(config, "API返回错误", data.get('message', '未知'))
            return None

        return data.get("data", {})


def parse_products(payload: dict) -> tuple[dict, list[dict]]:
    activities = payload.get("merchantActivities")
    if activities is None:
        activities = payload.get("merchant_activities", [])
    activities = activities or []

    activity = activities[0] if activities else {}
    props = activity.get("get_props", [])
    pets = activity.get("get_pets", [])

    now_ms = int(datetime.now(CN_TZ).timestamp() * 1000)
    products = []

    def is_active(item: dict) -> bool:
        start_time = item.get("start_time")
        end_time = item.get("end_time")
        if start_time is None or end_time is None:
            return True
        try:
            return int(start_time) <= now_ms < int(end_time)
        except (TypeError, ValueError):
            return True

    for item in props:
        if not is_active(item):
            continue
        products.append({
            "name": item.get("name", "未知商品"),
            "image": item.get("icon_url", ""),
            "type": "道具",
            "time_label": format_time_window(item.get("start_time"), item.get("end_time")),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "item_id": item.get("_id", ""),
        })

    for item in pets:
        if not is_active(item):
            continue
        products.append({
            "name": item.get("name", "未知精灵"),
            "image": item.get("icon_url") or item.get("main_url", ""),
            "type": "精灵",
            "time_label": format_time_window(item.get("start_time"), item.get("end_time")),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "item_id": item.get("_id", ""),
            "real_pet_id": item.get("real_pet_id", ""),
            "form_name": item.get("form_name", ""),
        })

    return activity, products


# ══════════════════════════════════════════════════════
#  变更检测（用于邮件去重）
# ══════════════════════════════════════════════════════

def _hash_path() -> Path:
    return SCRIPT_DIR / ".merchant_last_hash"


def _load_last_hash() -> str | None:
    p = _hash_path()
    if p.exists():
        return p.read_text().strip()
    return None


def _save_last_hash(h: str):
    _hash_path().write_text(h)


def _products_hash(products: list) -> str:
    raw = json.dumps([(p.get("name"), p.get("time_label")) for p in products],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ══════════════════════════════════════════════════════
#  明文日志
# ══════════════════════════════════════════════════════

LOG_PATH = SCRIPT_DIR / "merchant.log"


def write_merchant_log(activity: dict, products: list, round_info: dict,
                       matched: list | None = None):
    """将本轮商品信息写入明文日志。"""
    now_str = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"[{now_str}] {'='*50}")
    lines.append(f"活动: {activity.get('name', '远行商人')}")
    lines.append(f"日期: {round_info.get('date', '未知')}")
    lines.append(f"轮次: 第{round_info.get('current', '?')}/{round_info.get('total', 4)}轮")
    lines.append(f"商品数: {len(products)}")
    if matched:
        lines.append(f"命中关注: {'、'.join(matched)}")
    lines.append("-" * 40)

    for i, p in enumerate(products, 1):
        extra = ""
        if p.get("type") == "精灵":
            pid = p.get("real_pet_id", "")
            fname = p.get("form_name", "")
            extras = []
            if pid:
                extras.append(f"pet_id={pid}")
            if fname:
                extras.append(f"形态={fname}")
            if extras:
                extra = " (" + ", ".join(extras) + ")"
        lines.append(f"  {i}. [{p['type']}] {p['name']}{extra}  |  {p['time_label']}")

    lines.append("")
    text = "\n".join(lines) + "\n"

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"[日志] 写入失败: {e}")


# ══════════════════════════════════════════════════════
#  终端展示
# ══════════════════════════════════════════════════════

def print_header(activity: dict, round_info: dict, product_count: int):
    title = activity.get("name", "远行商人")
    subtitle = activity.get("start_date", "每日 08:00 / 12:00 / 16:00 / 20:00 刷新")
    print()
    print(f"  {BOLD}{MAGENTA}╔══ {title} ══╗{RESET}")
    print(f"  {DIM}║{RESET}  {subtitle}")
    print(f"  {DIM}║{RESET}  当前商品数: {BOLD}{product_count}{RESET}  |  "
          f"第 {BOLD}{round_info['current'] or '未开放'}{RESET}/{round_info['total']} 轮  |  "
          f"剩余 {BOLD}{round_info['countdown']}{RESET}")
    print(f"  {DIM}╚{'═' * 40}{RESET}")
    print()


def print_products(products: list[dict], watched: list[str]):
    for i, p in enumerate(products, 1):
        icon = "[道]" if p["type"] == "道具" else "[宠]"
        flag = f" {RED}★关注{RESET}" if any(w in p["name"] for w in watched) else ""
        print(f"  {BOLD}{i}.{RESET} {icon}  {BOLD}{YELLOW}{p['name']}{RESET}{flag}")
        print(f"     {DIM}类型:{RESET} {p['type']}  {DIM}时间:{RESET} {p['time_label']}")


# ══════════════════════════════════════════════════════
#  邮件发送
# ══════════════════════════════════════════════════════

def _send_email(config: dict, products: list[dict]):
    try:
        from email_sender import send_merchant_email
        send_merchant_email(config, products)
    except ImportError as e:
        print(f"[邮件] 导入 email_sender 失败: {e}")


def _send_alert(config: dict, error_type: str, error_msg: str):
    """查询失败时发送告警邮件。"""
    try:
        from email_sender import send_alert_email
        send_alert_email(config, error_type, error_msg)
    except ImportError as e:
        print(f"[告警] 导入 email_sender 失败: {e}")


# ══════════════════════════════════════════════════════
#  运行模式
# ══════════════════════════════════════════════════════

async def run_display(config: dict, refresh: bool = False, raw_json: bool = False):
    """仅查询并显示商品信息（不发送邮件）。"""
    payload = await fetch_merchant_info(config, refresh=refresh)
    if payload is None:
        sys.exit(1)

    if raw_json:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    activity, products = parse_products(payload)
    round_info = current_merchant_round()
    watched = [w.strip() for w in config.get("merchant", {}).get("watched_items", []) if w.strip()]

    print_header(activity, round_info, len(products))

    if not products:
        print(f"  {DIM}本轮暂无商品。{RESET}\n")
        write_merchant_log(activity, products, round_info)
        return

    print_products(products, watched)
    print()
    matched = [w for w in watched if any(w in p["name"] for p in products)]
    if matched:
        print(f"  {RED}命中关注道具: {'、'.join(matched)}{RESET}")
    names = "、".join([p["name"] for p in products])
    print(f"  {BOLD}当前商品:{RESET} {names}")
    print(f"  {BOLD}轮次:{RESET} 第{round_info['current']}轮  {BOLD}剩余:{RESET} {round_info['countdown']}")
    print()

    write_merchant_log(activity, products, round_info, matched)


async def run_once_with_email(config: dict):
    """执行一次检查并发送邮件通知。"""
    ts = datetime.now(CN_TZ).strftime("%H:%M:%S")
    print(f"[{ts}] 正在获取远行商人数据...")
    payload = await fetch_merchant_info(config, refresh=True)
    if payload is None:
        return

    activity, products = parse_products(payload)
    round_info = current_merchant_round()
    watched = [w.strip() for w in config.get("merchant", {}).get("watched_items", []) if w.strip()]

    print_header(activity, round_info, len(products))

    if not products:
        print(f"  {DIM}本轮暂无商品，跳过发送。{RESET}\n")
        write_merchant_log(activity, products, round_info)
        return

    print_products(products, watched)
    matched = [w for w in watched if any(w in p["name"] for p in products)]
    if matched:
        print(f"\n  {RED}命中关注道具: {'、'.join(matched)}{RESET}")

    hash_key = _products_hash(products)
    last_hash = _load_last_hash()

    write_merchant_log(activity, products, round_info, matched)

    if last_hash == hash_key:
        print("  商品无变化，跳过发送邮件。")
        return

    _send_email(config, products)
    _save_last_hash(hash_key)


async def run_daemon(config: dict):
    """持续运行，按时间表 8:01/12:01/16:01/20:01 轮询。"""
    print(f"{CYAN}远行商人邮件监控已启动{RESET}")
    print(f"检查时间：{', '.join(f'{h:02d}:{CHECK_MINUTE:02d}' for h in CHECK_HOURS)}（北京时间）")
    print("按 Ctrl+C 退出\n")

    # 启动时立即检查一次
    print("=== 启动检查 ===")
    await run_once_with_email(config)

    while True:
        next_time = _calc_next_check_time()
        wait_seconds = (next_time - datetime.now(CN_TZ)).total_seconds()
        print(f"\n下次检查：{next_time.strftime('%Y-%m-%d %H:%M')} "
              f"（约 {int(wait_seconds // 60)} 分钟后）")
        await asyncio.sleep(wait_seconds)
        print(f"\n=== {next_time.strftime('%H:%M')} 检查 ===")
        await run_once_with_email(config)


def _calc_next_check_time() -> datetime:
    now = datetime.now(CN_TZ)
    today = now.date()
    for h in CHECK_HOURS:
        t = datetime(today.year, today.month, today.day, h, CHECK_MINUTE, tzinfo=CN_TZ)
        if t > now:
            return t
    tomorrow = today + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                    CHECK_HOURS[0], CHECK_MINUTE, tzinfo=CN_TZ)


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="远行商人监控 — 支持终端查询与邮件通知")
    parser.add_argument("--refresh", "-r", action="store_true", help="强制刷新并显示当前商品（不发送邮件）")
    parser.add_argument("--json", "-j", action="store_true", help="输出原始 JSON 数据")
    parser.add_argument("--once", action="store_true", help="检查一次并发送邮件通知，然后退出")
    args = parser.parse_args()

    config = load_config()
    api_key = config.get("api", {}).get("api_key", "")
    if not api_key:
        print("[错误] 请在 config.yaml 中填写 api_key")
        sys.exit(1)

    if args.once:
        await run_once_with_email(config)
    elif args.refresh or args.json:
        await run_display(config, refresh=args.refresh, raw_json=args.json)
    else:
        await run_daemon(config)


if __name__ == "__main__":
    asyncio.run(main())
