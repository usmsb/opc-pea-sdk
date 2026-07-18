# OPC PEA SDK

Reusable SDK and reference templates for OPC Personal Economic Agents (PEA).

This repository is generated from `usmsb/opc-platform`; the platform repository
is the canonical source of truth. Source ref: `64b9920417a7199cd6be42721e537e0f653b655e`.

## Install

```bash
pip install -e .
```

The Python package name is `opc-pea-sdk`; import it as `pea_core`.

## Core Modules

- `pea_core.harness`: 共享 Harness 父类：perceive -> think -> act -> observe。
- `pea_core.context`: 分层上下文、工具结果压缩、交付物正文隔离。
- `pea_core.memory`: 向量记忆、跨会话召回、滚动摘要。
- `pea_core.admin`: 老板后台登录、实体自省、CRUD、媒体上传。
- `pea_core.embeddings`: 沙箱向量与 MiniMax embedding provider。
- `pea_core/web`: 可复用管理端页面与 Markdown 渲染资产。

## Reference Cases

- **雕刻时光** (`diaokeshiguang`): 模板 A · 创意生产，歌曲、MV、祝福、纪念内容等高情绪价值定制，内容生产卖方，可被其它 PEA 转包调用。
- **喵星球** (`miaoxingqiu`): 模板 B · 垂直服务，零售、上门服务、会员卡、纪念碑与本地生活履约，宠物顾问卖方，也可采购内容 Agent。
- **底牌堂 / 企诊通** (`dipaitang`): 模板 C · 诊断陪跑，企业诊断、整改方案、陪跑、顾问交付与人工承接，经营顾问卖方，也可向内容/设计 Agent 派单。

## Standard PEA Contract

Every deployable PEA should remain an independent service and expose:

- `GET /health`
- `GET /.well-known/agent.json`
- `POST /a2a`

The OPC backend dispatches through A2A HTTP. The SDK only provides reusable
building blocks; business tools, payment credentials, SQLite files, browser
profiles and deployment secrets remain in each PEA instance.

## Sync Policy

Run the exporter from the OPC platform repository:

```bash
python scripts/sync_pea_sdk_repo.py --target /path/to/opc-pea-sdk --clean
```

GitHub Actions in `usmsb/opc-platform` also exports and pushes this tree when
`peas/pea_core`, PEA example READMEs or SDK sync metadata changes.
