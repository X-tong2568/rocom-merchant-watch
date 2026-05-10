"""
邮件发送模块
==========
通过 QQ 邮箱 SMTP 发送远行商人上新通知。
使用标准库 smtplib + email，无需额外依赖。
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, timezone
from typing import List, Dict

CN_TZ = timezone(timedelta(hours=8))


def _build_html_body(products: List[dict], matched: List[str]) -> str:
    """构建邮件 HTML 正文：商品列表表格。"""
    now_str = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    rows = ""
    for p in products:
        name = p.get("name", "未知")
        ptype = p.get("type", "道具")
        time_label = p.get("time_label", "—")
        extra = ""
        if ptype == "精灵":
            pid = p.get("real_pet_id", "")
            fname = p.get("form_name", "")
            if pid or fname:
                parts = []
                if pid: parts.append(f"ID: {pid}")
                if fname: parts.append(f"形态: {fname}")
                extra = f"<br><span style='font-size:11px;color:#999;'>{' / '.join(parts)}</span>"
        rows += f"<tr><td style='padding:8px 12px;border-bottom:1px solid #eee;'>{name}{extra}</td><td style='padding:8px 12px;border-bottom:1px solid #eee;color:#888;'>{time_label}</td></tr>"

    matched_block = ""
    if matched:
        items = "、".join(matched)
        matched_block = f"<p style='color:#e74c3c;font-weight:bold;'>命中关注道具：{items}</p>"

    return f"""<!DOCTYPE html>
<html><body style='font-family:"Microsoft YaHei",sans-serif;padding:20px;'>
  <div style='max-width:600px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);'>
    <div style='background:#2c3e50;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0;'>
      <h2 style='margin:0;font-size:18px;'>远行商人上新</h2>
      <p style='margin:4px 0 0;font-size:12px;opacity:.8;'>刷新时间：{now_str}</p>
    </div>
    <div style='padding:16px 20px;'>
      {matched_block}
      <table style='width:100%;border-collapse:collapse;margin-top:8px;'>
        <thead><tr style='background:#f5f5f5;'>
          <th style='padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;'>商品名称</th>
          <th style='padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;'>时间段</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style='margin-top:16px;font-size:12px;color:#aaa;'>共 {len(products)} 件商品</p>
    </div>
  </div>
</body></html>"""


def send_alert_email(config: dict, error_type: str, error_msg: str) -> bool:
    """查询失败时发送告警邮件。

    Args:
        config: 完整配置字典
        error_type: 错误类型（如 请求超时、HTTP错误、JSON解析失败）
        error_msg: 详细错误信息

    Returns:
        bool: 发送成功返回 True
    """
    email_cfg = config.get("email", {})
    sender = email_cfg.get("sender", "")
    password = email_cfg.get("password", "")
    recipients = email_cfg.get("alert_recipients", [])

    if not sender or not password or not recipients:
        print("[告警] 邮件配置不完整，跳过发送告警")
        return False

    now_str = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    html_body = f"""<!DOCTYPE html>
<html><body style='font-family:"Microsoft YaHei",sans-serif;padding:20px;'>
  <div style='max-width:600px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);'>
    <div style='background:#e74c3c;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0;'>
      <h2 style='margin:0;font-size:18px;'>远行商人查询告警</h2>
      <p style='margin:4px 0 0;font-size:12px;opacity:.8;'>告警时间：{now_str}</p>
    </div>
    <div style='padding:20px;'>
      <table style='width:100%;border-collapse:collapse;'>
        <tr><td style='padding:10px 12px;border-bottom:1px solid #eee;color:#888;width:80px;'>错误类型</td><td style='padding:10px 12px;border-bottom:1px solid #eee;color:#e74c3c;font-weight:bold;'>{error_type}</td></tr>
        <tr><td style='padding:10px 12px;color:#888;'>错误详情</td><td style='padding:10px 12px;color:#333;'>{error_msg}</td></tr>
      </table>
      <p style='margin-top:20px;font-size:12px;color:#aaa;'>请检查 API 服务状态或网络连接。</p>
    </div>
  </div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr(("远行商人告警", sender))
    msg["To"] = formataddr((None, ", ".join(recipients)))
    msg["Subject"] = Header(f"【告警】远行商人查询失败 - {error_type}", "utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(email_cfg.get("smtp_host", "smtp.qq.com"),
                              email_cfg.get("smtp_port", 465)) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"[告警] 邮件发送成功 → {', '.join(recipients)}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("[告警] 发送失败：SMTP 认证失败")
        return False
    except Exception as e:
        print(f"[告警] 发送失败：{e}")
        return False


def send_merchant_email(config: dict, products: List[dict]) -> bool:
    """发送远行商人邮件通知。

    Args:
        config: 完整配置字典（含 email 和 merchant 段）
        products: 当前轮的商品列表

    Returns:
        bool: 发送成功返回 True
    """
    email_cfg = config.get("email", {})
    sender = email_cfg.get("sender", "")
    password = email_cfg.get("password", "")
    recipients = email_cfg.get("recipients", [])

    if not sender or not password or not recipients:
        print("[邮件] 配置不完整，跳过发送（请检查 config.yaml 中 email 段）")
        return False

    watched = [w.strip() for w in config.get("merchant", {}).get("watched_items", []) if w.strip()]
    product_names = [p.get("name", "") for p in products]

    # 命中关注道具
    matched = [w for w in watched if any(w in pn for pn in product_names)]

    if matched:
        subject = f"【远行商人上新】远神神了！：{'、'.join(matched)}"
    else:
        subject = "【远行商人上新】本轮商品列表"

    html_body = _build_html_body(products, matched)

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr(("远行商人监控", sender))
    msg["To"] = formataddr((None, ", ".join(recipients)))
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(email_cfg.get("smtp_host", "smtp.qq.com"),
                              email_cfg.get("smtp_port", 465)) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"[邮件] 发送成功 → {', '.join(recipients)}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("[邮件] 发送失败：SMTP 认证失败，请检查 QQ 邮箱授权码是否正确")
        return False
    except Exception as e:
        print(f"[邮件] 发送失败：{e}")
        return False
