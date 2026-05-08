# rocom-merchant-watch

> 洛克王国「远行商人」商品定时监控 + 邮件通知 + 查询失败告警

## 参考来源

- **数据接口**：基于 WeGame Rocom API，由 `shallow.ink` 提供（`https://wegame.shallow.ink`）
- **原始项目**：[astrbot_plugin_rocom](https://github.com/Entropy-Increase-Team/astrbot_plugin_rocom) by **bvzrays & 熵增项目组**（洛克王国数据查询 AstrBot 插件 v3.0.0）
- 本项目提取了原插件中的远行商人查询逻辑，独立为可定时运行的监控脚本，并扩展了邮件告警功能

## 贡献说明

本项目由 **X-tong2568** 与 **Claude (Anthropic)** 协作完成。

| 贡献者 | 内容 |
|--------|------|
| X-tong2568 | 项目需求、配置提供、测试验证、发布维护 |
| Claude | 查询失败告警功能（`send_alert_email` + merchant 错误分支集成）、`config.example.yaml` 模板、`.gitignore`、`readme.md`、LICENSE 添加、代码审查

## 数据接口

| 项目 | 说明 |
|------|------|
| 接口地址 | `GET https://wegame.shallow.ink/api/v1/games/rocom/merchant/info` |
| 认证方式 | 请求头 `X-API-Key` 传入 API Key |
| 参数 | `refresh=true/false` 控制是否强制刷新缓存 |
| 返回格式 | `{ code: 0, data: { merchantActivities: [...] } }` |
| 商品数据结构 | `merchantActivities[0].get_props`（道具）、`merchantActivities[0].get_pets`（精灵） |

## 实现原理

### 定时轮询
- 每天北京时间 **08:01 / 12:01 / 16:01 / 20:01** 自动拉取商品数据
- 使用 `asyncio.sleep` 计算距下一次检查的等待时间，不依赖系统 cron

### 变更检测
- 对当前商品列表（名称 + 时间段）做 MD5 哈希
- 与上次发送邮件时保存的哈希比对，无变化则跳过，避免重复通知

### 邮件通知
- 通过 QQ 邮箱 SMTP（`smtp.qq.com:465`，SSL）发送 HTML 邮件
- 邮件内容包含商品名称、类型、时间段，命中关注道具时特别标注

### 查询失败告警
- 以下 5 种情况自动向告警邮箱发送告警邮件：
  1. 请求超时
  2. 网络请求失败
  3. HTTP 状态码异常
  4. JSON 解析失败
  5. API 返回业务错误码
- 告警邮件与上新通知走同一套 SMTP 配置，收件人可独立设置

## 文件说明

| 文件 | 作用 |
|------|------|
| `merchant.py` | 主脚本，API 拉取 + 数据解析 + 定时调度 + 变更检测 + 告警触发 |
| `login.py` | QQ/微信扫码登录脚本，获取 frameworkToken 等凭证 |
| `email_sender.py` | 邮件发送模块，包含上新通知 `send_merchant_email` 和失败告警 `send_alert_email` |
| `config.yaml` | 配置文件（API Key、邮件 SMTP、关注道具、收件人） |
| `token.json` | 登录凭证缓存（由 `login.py` 生成） |
| `requirements.txt` | Python 依赖清单 |

## 环境要求

- Python 3.10+
- 依赖安装：`pip install -r requirements.txt`

## 使用方法

```bash
# 持续运行（定时轮询 + 邮件通知）
python merchant.py

# 只检查一次并发送邮件（适合 cron / 计划任务）
python merchant.py --once

# 仅查看当前商品（不发送邮件）
python merchant.py --refresh

# 输出原始 JSON 数据
python merchant.py --json

# QQ 扫码登录（获取凭证）
python login.py

# 微信扫码登录
python login.py --wechat
```

## 配置说明

编辑 `config.yaml`：

```yaml
api:
  base_url: "https://wegame.shallow.ink"
  api_key: "你的API-Key"      # 必填，从服务提供方获取

merchant:
  watched_items:              # 关注道具，命中时邮件标题会特别标注
    - "国王球"
    - "棱镜球"

email:
  smtp_host: "smtp.qq.com"
  smtp_port: 465
  sender: "发件人@qq.com"
  password: "QQ邮箱SMTP授权码"  # 非 QQ 密码，在 QQ 邮箱设置中生成
  recipients:                  # 上新通知收件人
    - "收件人@qq.com"
  alert_recipients:            # 查询失败告警收件人
    - "告警收件人@foxmail.com"
```
