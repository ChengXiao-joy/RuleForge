# 开源发布检查清单（release_checklist）

发布前逐项勾选，全部通过方可发布。

## 1. 密钥安全

| # | 检查项 | 状态 |
| --- | --- | --- |
| 1.1 | `.env` 已加入 .gitignore，不会提交 | ☐ |
| 1.2 | `.env` 备份（`.env_*` / `.env.*`）也全部忽略 | ☐ |
| 1.3 | 仓库内无任何真实密钥（搜 `sk-` / `ANTHROPIC_API_KEY=` 仅出现在 .env.example 占位符） | ☐ |
| 1.4 | `.env.example` 只含占位符 `sk-ant-xxxxxxxx` | ☐ |

## 2. 缓存与临时文件清理

| # | 检查项 | 状态 |
| --- | --- | --- |
| 2.1 | `__pycache__/`、`.pytest_cache/` 未提交（已忽略） | ☐ |
| 2.2 | `logs/`、`storage/*.db`、`*.log`、`rules*.json` 未提交 | ☐ |
| 2.3 | 无遗留调试脚本 / 临时文件在根目录 | ☐ |

## 3. 测试状态

| # | 检查项 | 状态 |
| --- | --- | --- |
| 3.1 | `python -m pytest tests/ -v` → 127 passed | ☐ |
| 3.2 | `python scripts/self_test.py` → 8/8 通过 | ☐ |
| 3.3 | `python cli.py selftest` → 4 项通过 | ☐ |

## 4. 文档完整性

| # | 检查项 | 状态 |
| --- | --- | --- |
| 4.1 | README.md（简介/用户/能力/架构/启动/配置/排查/开发说明/目录树） | ☐ |
| 4.2 | docs/PRD.md、docs/rule_schema.md、docs/验收手册.md | ☐ |
| 4.3 | docs/deploy.md（本地部署 + 外网临时访问 + 报错处理） | ☐ |
| 4.4 | docs/CHANGELOG.md、docs/demo_guide.md | ☐ |
| 4.5 | LICENSE（MIT） | ☐ |
| 4.6 | requirements.txt 锁定版本 | ☐ |

## 5. 样例可用性

| # | 检查项 | 状态 |
| --- | --- | --- |
| 5.1 | docs/samples/office_rules.json 可导入，自检全通过 | ☐ |
| 5.2 | docs/samples/conflict_rules.json 可导入，扫描出 2 条互斥冲突 | ☐ |
| 5.3 | docs/samples/sandbox_rules.json 可导入，沙箱推演命中链路正确 | ☐ |

## 6. 发布动作（最后执行）

- [ ] 确认默认分支无未提交的密钥/缓存
- [ ] 打 Tag（如 `v1.0`）
- [ ] 填写仓库简介、README 首屏截图、License 标注
- [ ] 关联 docs/CHANGELOG.md 到 Releases 说明

---

发布负责人：____________　　日期：____________
