# 部署说明（deploy）

## 一、本地单机部署（推荐，开箱即用）

**环境要求**

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（其它平台可参考，脚本为 Windows bat） |
| Python | 3.10+（安装时勾选 "Add Python to PATH"） |
| 网络 | 需能访问 LLM API 端点（官方或自定义 `ANTHROPIC_BASE_URL`） |
| 存储 | 本地 SQLite（无需安装数据库服务） |

**启动步骤**

```bat
:: 方式一：一键启动（自动建虚拟环境→装依赖→查配置→启动网页）
双击项目根目录 run.bat

:: 方式二：手动
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          :: 编辑 .env 填入 ANTHROPIC_API_KEY
.venv\Scripts\python -m streamlit run ui/app.py
```

**端口**：默认 `8501`（Streamlit 默认）。改端口：

```bat
.venv\Scripts\python -m streamlit run ui/app.py --server.port 8080
```

**环境限制**

- 单机单用户：无登录/多用户权限，SQLite 本地存储，不适合多人并发共用；
- 密钥存本地 `.env`（已被 .gitignore 忽略，勿提交）；
- LLM 相关功能依赖 API 网络可用。

## 二、简单外网临时访问方案（演示/临时用，非正式上线）

项目是本地 Streamlit 应用，没有内建公网能力。临时对外演示可用内网穿透工具，把本地 `8501` 端口暴露出去：

| 方案 | 命令示例 | 说明 |
| --- | --- | --- |
| cloudflared | `cloudflared tunnel --url http://localhost:8501` | Cloudflare 官方，免费临时公网域名 |
| ngrok | `ngrok http 8501` | 需注册 token，简单稳定 |
| frp | 配置 `frpc.ini` 转发 8501 | 自建服务器，可控性强 |

> ⚠️ 注意：① 内网穿透会把本地应用暴露公网，仅限临时演示，用完立即关闭；② 生产使用请按正式部署方案（鉴权、HTTPS、反向代理）另行设计，本项目**不包含**云端部署与域名上线能力。

## 三、常见报错处理

### 403 Forbidden：Request not allowed

- **现象**：调用 LLM 返回 `403 - Request not allowed`
- **原因**：网络出口 IP 被 API 网关（Cloudflare）拒绝（常见于大陆直连或数据中心 IP）
- **处理**：① 运行 `python scripts/network_check.py` 连测；② 换干净代理节点/网络；③ 在 `.env` 配置兼容 Anthropic 协议的中转端点 `ANTHROPIC_BASE_URL=...`（注意中转方可见请求内容）

### SSL 证书报错（SELF_SIGNED_CERT / CERTIFICATE_VERIFY_FAILED）

- **原因**：代理工具开启 TLS 解密（MITM）或企业自签证书
- **处理**：① 关闭代理的 TLS 解密（推荐）；② 安装代理根证书到系统受信任根；③ 设环境变量 `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` 指向证书文件；④ 换不开启 MITM 的网络（手机热点）

### 请求超时 / 页面一直转圈

- 网络不稳时最坏 1~2 分钟内会明确报错；报错后重试即可；持续失败先跑 `scripts/network_check.py` 定位网络。
