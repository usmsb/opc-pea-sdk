# PEA Template Matrix

| Template | Reference | Best For | A2A Position |
|---|---|---|---|
| 模板 A · 创意生产 | 雕刻时光 | 歌曲、MV、祝福、纪念内容等高情绪价值定制 | 内容生产卖方，可被其它 PEA 转包调用。 |
| 模板 B · 垂直服务 | 喵星球 | 零售、上门服务、会员卡、纪念碑与本地生活履约 | 宠物顾问卖方，也可采购内容 Agent。 |
| 模板 C · 诊断陪跑 | 底牌堂 / 企诊通 | 企业诊断、整改方案、陪跑、顾问交付与人工承接 | 经营顾问卖方，也可向内容/设计 Agent 派单。 |

## Build Flow

1. Choose a template and decide whether the PEA sells, buys or does both over A2A.
2. Reuse the shared scaffold: three entrypoints, SQLite, creator console, admin and sandbox credits.
3. Replace domain tools, prompts, catalog and guardrails while keeping tool boundaries explicit.
4. Preserve `/health`, `/.well-known/agent.json` and `/a2a`.
5. Register the PEA in the OPC Agent Matrix with H5, Web, Mini Program, admin and creator entrypoints.
6. Run unit tests, HTTP funnel tests and matrix health checks before dispatch.
