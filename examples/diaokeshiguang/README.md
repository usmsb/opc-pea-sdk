# 雕刻时光 PEA（模板 A · 歌曲+MV 定制）

> 一个**自主 Agent（harness）**驱动的个人经济智能体：用户和创客都通过**一个对话框**完成一切，
> AI 推理 + 调工具，护栏在工具边界——不是规则引擎、不是按钮迷宫（见《pea需求/》第〇原则）。

## 这是什么

- **C 端**：和 AI 聊出一首专属歌（免费 20s 试听 → 99 完整歌 → 4999 歌+MV），改稿、交付、记忆。
- **创客端**：主理人用一个对话框经营生意（查数据 / 改价 / 发券）。
- **三端**：微信小程序（`miniprogram/`）、H5（`web/index.html`）、创客 PC 台（`web/creator.html`），**共用同一后端 API**。
- **A2A**：暴露 `/.well-known/agent.json`，可被兄弟 PEA（喵星球/底牌堂）转包调用做歌/MV（卖方）。

## 设计要点

| 维度 | 实现 |
|---|---|
| brain | AI 中枢（MiniMax-M3），`app/harness.py` 的 perceive→think→act→observe 循环 |
| 工具 | `app/tools/`：谱曲/歌词/质检/支付/记忆/改稿/MV/查数据/改价（Agent 调用，非按钮） |
| 歌曲生成 | **MiniMax Music 2.5**（`app/providers/minimax.py`），provider 抽象可换 Mureka |
| 护栏 | `app/guardrails.py`：免费3次/改稿次数/折扣上限/内容红线(LLM)/预算阀门（工具边界确定性） |
| 钱 | C 端微信支付（服务商子商户）；算力走 OPC 积分子账户（`app/credits.py`） |
| 数据 | 独立 SQLite（`app/models.py`），客户数据隔离 |

## 运行（沙箱：无需任何密钥即可跑通全功能）

```bash
cd peas/diaokeshiguang
pip install -r requirements.txt        # 或用已装好的 python3.12
./run.sh                               # → http://127.0.0.1:8099 (C端) ／ /creator.html (创客台)
```

> 不配 `DKSG_MINIMAX_API_KEY` 即 **sandbox 模式**：对话脑与歌曲生成用离线测试替身，
> 全链路（对话→生成→支付→交付→改稿→经营）功能完整、可演示可测。歌曲为占位 URL。

## 测试

```bash
python3.12 -m pytest                   # 单测：漏斗 + 护栏 + 积分 + 改价
python3.12 -m tests.run_e2e            # 离线全漏斗 trace + 断言
python3.12 -m tests.run_http           # 对运行中的服务跑 HTTP 全漏斗 + 断言
```

## 转生产（接真实凭证）

1. 配 `.env`（见 `.env.example`）：`DKSG_MINIMAX_API_KEY`（歌曲生成转真），`DKSG_WECHAT_SUB_MCHID`（支付转真）。
2. **两件部署前核对**：① MiniMax host（`api.minimaxi.com` vs `api.minimax.io`）② 书面商用授权。
3. 构建镜像并部署：

```bash
docker build -t opc-pea-a:0.1.0 .
# 渲染独立实例（独立 ns + 独立 DB），见 deploy/pea/
python ../../deploy/pea/render_pea_instance.py --config ../../deploy/pea/instances/diaokeshiguang.json | kubectl apply -f -
kubectl -n ns-pea-diaokeshiguang create secret generic pea-diaokeshiguang-env \
  --from-literal=DKSG_MINIMAX_API_KEY=... --from-literal=DKSG_WECHAT_SUB_MCHID=...
```

## 外部人工闸门（代码已就绪，等审批即生产）

- 微信**服务商进件 / 子商户**（京湾 ISV）：未下来时支付走 mock；下来后填 `DKSG_WECHAT_SUB_MCHID` 即真单。
- 微信**小程序提审发布**：`miniprogram/` 工程已就绪，授权第三方平台代提审后发布。
- MiniMax **商用授权 / 正式 host**。

## 范围

✅ **M1**：免费试听 + 99 完整歌全闭环 + 创客经营对话 + 积分扣费 + 三端 + A2A 名片。
✅ **M2**：MV 工具链（关键帧 gpt-image2 + vidu one_click_mv，矩阵适配器/沙箱）；视频 **90 条/3 月配额**
（周期重置、到顶停发加购、改稿不另计）；**A2A 卖方** `/a2a` JSON-RPC（`message/send`·`tasks/get`，
被喵星球/底牌堂转包做歌/MV）。见 `app/{quota,a2a_fulfill,providers/video}.py`、`tests/test_m2.py`。
✅ **M3**：分享裂变（`生成分享` + 裂变归因奖励）、复购（老客回访主动问候 + `记纪念日`/memory_dates 复购建议）、
H5 传播页 `web/share.html`（ref 归因）、记忆增强。见 `app/growth.py`、`app/tools/growth.py`、`tests/test_m3.py`。
✅ **M4**：C 端用户体系（JWT 鉴权 + 微信登录 `wx.login`/code2session，sandbox mock；访客→微信登录**在位升级保留作品**；接口鉴权堵住冒用）+ **多页面移动 App**（H5 与小程序均 4 tab：创作/作品/发现/我的）。见 `app/auth.py`、`app/wechat.py`、`app/routes/auth.py`、`web/index.html`、`miniprogram/pages/*`、`tests/test_auth.py`。
✅ **M5（信任与高客单承接）**：**案例展示 feed**（8 个标注场景的策划样片，覆盖老人/小孩/纪念日/企业）+ **信任背书**（已为 N 家定制/满意度）+ **产品价格页**（三档·改稿规则·交付周期，读实时价）+ **订单交付进度**（制作中/待确认/已交付）+ **售后入口** + **专属顾问入口**。见 `app/content.py`、`app/routes/content.py`、`tests/test_m5.py`。
⏭ 后续（**仅剩外部凭证**）：真歌（minimax.io 音乐 key）、MV 真矩阵联调、A2A 真实托管结算对接 OPC 中枢、版权/RWA。

## 目录

```
app/        后端：harness / tools / providers / guardrails / credits / routes
web/        H5(index.html) + 创客台(creator.html)
miniprogram/微信小程序工程
tests/      pytest + 离线/HTTP e2e
Dockerfile  独立镜像
```
