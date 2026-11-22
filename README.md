# RuleForge 规则工坊

> RuleForge 是面向业务、运营、人事、分析师及普通个人的自然语言规则编排工具。核心设计是让 LLM 只承担自然语言转结构化规则树、引擎结果转中文解释两项职责；单规则自检、冲突检测、沙箱推演、版本 Diff 与影响分析等确定性逻辑全部自研，避免幻觉。系统基于 Python、Streamlit、SQLite、Anthropic SDK 与 Pydantic，分为 llm_layer / rule_engine / ui / storage / utils 五层架构。

## 核心底层原则（红线）

- **LLM 只做两件事**：① 把自然语言翻译成符合规范的结构化规则树；② 把引擎输出的结构化结果翻译成通俗易懂的中文解释。
- **所有确定性逻辑自研**：冲突检测、沙箱仿真、规则树 Diff、判定推理、导入导出校验、统计计算全部用自研代码实现，不交给大模型判断，避免幻觉。

## 目标人群

| 角色 | 使用场景 |
| --- | --- |
| 人事 / 运营 / 业务人员 | 用大白话描述业务规则，系统自动翻译、自检、检测冲突，无需写代码 |
| 分析师 | 冲突报告、影响分析、版本对比，看清规则间的关系与变更 |
| 普通个人 | 给自己立生活小规则（如「气温低于10度提醒我加衣」） |
| 开发者 / 面试官 | 展示「LLM 只做翻译、确定性逻辑全自研」的工程边界设计 |

## 功能一览

| 模块 | 说明 |
| --- | --- |
| 自然语言建规则 | 输入大白话 → Claude 翻译为结构化规则树（JSON 校验 + 最多2次重试兜底） |
| 规则管理 | 规则增删改查、结构化规则树预览、编辑 JSON、SQLite 持久化 |
| 单规则自检 | 条件为空/字段非法/表达式语法错误/单条内部死条件，保存时自动触发 |
| 多规则冲突检测 | 互斥/覆盖/冗余三类冲突，批量扫描，冲突报告落库 |
| 版本管理 | 每次编辑自动快照（修改前后完整 JSON）、手动打标签、任选两版本 Diff |
| 沙箱推演 | 输入样本批量匹配规则，按优先级输出命中链路与最终结论，完全隔离 |
| 影响分析 | 识别被覆盖的下游、冲突、依赖本规则输出的关联规则 |
| 批量导入导出 | JSON 导出（含版本快照），导入自动自检拦截坏数据 + 导入报告 |
| 数据看板 | 规则/异常/冲突/版本/推演五大统计指标 + 全功能导航入口 |

## 环境依赖

- Python 3.10+
- 依赖包见 `requirements.txt`：`anthropic`、`streamlit`、`pydantic`、`python-dotenv`、`pytest`
- 无任何重型外部服务：数据库为本地 SQLite，一键启动

## 快速启动

**方式一：一键启动（推荐，Windows）**

双击项目根目录的 **`run.bat`** —— 自动完成：创建虚拟环境 → 安装依赖 → 检查 .env → 启动网页。首次运行请按提示先填写 `.env` 中的 `ANTHROPIC_API_KEY`。

**方式二：手动启动**

```bash
# 1. 安装依赖（requirements.txt 已锁定版本）
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. 配置环境
cp .env.example .env
# 编辑 .env：填入 ANTHROPIC_API_KEY（以及可选的自定义端点 ANTHROPIC_BASE_URL / 模型 ANTHROPIC_MODEL）

# 3. 启动（任选）
.venv\Scripts\streamlit run ui/app.py        # Streamlit 页面
.venv\Scripts\python cli.py selftest        # 离线自检（无需 Key）
```

## .env 配置说明

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | ✅ | LLM 服务密钥 |
| `ANTHROPIC_BASE_URL` | ❌ | 自定义 API 端点（默认官方地址；网络无法直连时可指向兼容 Anthropic 协议的中转端点） |
| `ANTHROPIC_MODEL` | ❌ | 模型 ID（默认 claude-opus-5-5） |
| `LLM_MAX_TOKENS` / `LLM_EFFORT` / `LLM_TIMEOUT` | ❌ | 调用参数（默认 8192 / medium / 60s） |
| `JSON_MAX_RETRIES` | ❌ | 解析格式错误最多重试次数（默认 2） |
| `DB_PATH` / `LOG_DIR` / `LOG_LEVEL` | ❌ | 存储与日志路径 |

> `.env` 已被 `.gitignore` 忽略，密钥不会提交到 Git。

## 常见故障排查

### 403 Forbidden：Request not allowed

- **现象**：调用 LLM 返回 `403 - {'error': {'type': 'forbidden', 'message': 'Request not allowed'}}`
- **原因**：网络出口 IP 被 API 网关（Cloudflare）拒绝——常见于中国大陆网络直连、或代理/VPN 使用了被拉黑的数据中心 IP
- **排查**：运行 `python scripts/network_check.py` 连测多次：
  - 全部失败 → 换代理节点（优先住宅/原生 IP）或换网络（如手机热点）；
  - 全部成功 → 网络正常，检查 `.env` 里 `ANTHROPIC_BASE_URL` 是否配置错误。
- **兜底**：在 `.env` 中配置兼容 Anthropic 协议的中转端点 `ANTHROPIC_BASE_URL=...`（注意中转方可见请求内容，选可信服务）

### SSL 证书报错：SELF_SIGNED_CERT / CERTIFICATE_VERIFY_FAILED

- **现象**：请求报 `SSL: CERTIFICATE_VERIFY_FAILED`、`self-signed certificate in certificate chain`
- **原因**：代理工具（Clash / v2rayN 等）开启了 TLS 解密（MITM），或企业网络使用自签证书，Python 无法验证其根证书
- **方案A（推荐）**：关闭代理工具的 TLS 解密功能；
- **方案B**：把代理导出的根证书安装到 Windows「受信任的根证书颁发机构」；
- **方案C**：设置环境变量指向该证书文件：
  ```cmd
  set SSL_CERT_FILE=C:\path\to\proxy-cert.pem
  set REQUESTS_CA_BUNDLE=C:\path\to\proxy-cert.pem
  ```
- **方案D**：换用不开启 MITM 的网络环境（如手机热点）。

### 页面一直转圈 / 请求超时

- 网络不稳时最坏约 1~2 分钟会明确报错（已配置快速失败）；
- 报错后点重试即可；持续失败请先跑 `scripts/network_check.py` 确认网络。

## 开源协议

本项目使用 [MIT License](LICENSE)。

## 项目架构

```
llm_layer/      与 Claude 交互：提示词、客户端、JSON 解析与重试、结果翻译（explainer）
rule_engine/    规则引擎：Schema（冻结）、单规则自检、冲突检测、版本 Diff、沙箱推演、影响分析
ui/             Streamlit 页面：看板首页、新增、列表、详情、冲突报告、版本历史、沙箱、导入导出
storage/        SQLite 持久化：规则、冲突、版本快照、推演记录、解析日志
utils/          通用工具：配置、日志、批量导入导出
```

## 命令行工具

```bash
python cli.py parse "如果员工本月迟到超过3次，就发邮件提醒人事部门" [--save]
python cli.py list
python cli.py check <rule_id>      # 单条自检
python cli.py scan                 # 批量冲突扫描
python cli.py export [--ids A,B] [--out rules.json]
python cli.py import rules.json
python cli.py stats                # 看板统计
python cli.py selftest             # 离线自检
```

## 测试

```bash
pytest tests/ -v                     # 全部单元测试（离线，无需 API Key）
python tests/run_parser_eval.py      # 解析验收评测（需 API Key）
python scripts/network_check.py      # API 网络连通性自检
```

## 开发说明

- **测试**：`python -m pytest tests/ -v`（127 项）；自动化自测 `python scripts/self_test.py`（8 项核心链路，临时库不污染数据）
- **分层约定**：`llm_layer`（LLM 只做翻译）/ `rule_engine`（自研确定性逻辑）/ `ui`（Streamlit）/ `storage`（SQLite）/ `utils`（工具）
- **核心红线**：LLM 只做「自然语言→规则树」「结果→中文解释」两件事；冲突检测、单规则自检、沙箱推演、版本 Diff、导入校验与统计全部自研代码实现
- **代码规范**：模块化、中文注释、函数标注入参出参、优先功能正确

## 仓库目录树

```
RuleForge/
├── run.bat               # 一键启动（建环境→装依赖→查配置→启动网页）
├── cli.py                # 命令行入口（parse/list/check/scan/export/import/stats/selftest）
├── requirements.txt      # 锁定版本依赖
├── LICENSE               # MIT 开源协议
├── README.md
├── .env.example          # 配置模板（.env 由用户本地创建，已 gitignore）
├── llm_layer/            # LLM 交互层：提示词、客户端、解析、结果翻译
├── rule_engine/          # 规则引擎层：Schema、自检、冲突、Diff、推演、影响分析
├── ui/                   # Streamlit 页面
├── storage/              # SQLite 持久化
├── utils/                # 通用工具：配置、日志、导入导出
├── scripts/              # 启动 / 自测 / 网络检测脚本
├── shortcuts/            # Windows 双击快捷方式（.bat）
├── tests/                # 自动化测试
└── docs/                 # PRD / 数据 Schema / 验收手册
```

## 各阶段交付记录

- 阶段0：需求底座（PRD / Schema 冻结 / Claude API / SQLite / 解析重试 / 测试用例库）
- 阶段1：规则基础管理与单规则自检
- 阶段2：多规则冲突检测引擎 + 冲突报告
- 阶段3：规则版本管理、快照、Diff 对比
- 阶段4：沙箱仿真推演 + 影响范围分析
- 阶段5：批量导入导出 + 数据看板 + 项目收尾（原型完成）
