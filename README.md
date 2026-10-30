# 一键铺货系统（淘宝 / 天猫 / 1688 / 京东）规划与技术骨架

> 目标：输入一个商品链接（或一堆链接 / 一个店铺 / 一个类目），自动抓下**标题、主图、SKU 规格、销售属性、参数表、详情图文、价格、库存、类目**，转换成自家店铺可发布的数据，一键（或批量）上架。

---

## 0. 先回答你最关心的三个问题

### Q1：那些铺货软件是不是通过 API 实现的？

**不是单一 API，主流是三类技术的混合，而且采集端和发布端用的根本不是同一套东西。**

| 环节 | 铺货软件实际用什么 | 说明 |
|---|---|---|
| **采集（抓别人的宝贝）** | ① 页面/接口逆向（主流）② 官方开放平台 API（少数） | 开放平台的 `taobao.item.get` **只能读自己店铺或已授权商品**，读不了任意别人的在售商品。所以采集端 99% 是逆向：解析详情页 HTML 里的内嵌 JSON，或者直接调 `h5api.m.taobao.com` 的 mtop 接口（带 sign 签名）。 |
| **发布（上到自己店铺）** | ① 商家后台协议模拟（主流）② 官方开放平台 API（正规服务商） | 后台模拟 = 用**你自己店铺的登录态**（Cookie / `_tb_token_` / `x5sec`）去重放"发布宝贝"那个请求。开放平台 API（`taobao.item.add.v2`）需要企业资质、类目授权、应用审核，个人店基本拿不到。 |
| **图片** | 直链下载 + 转存 | `img.alicdn.com` / `cbu01.alicdn.com` / `360buyimg.com` 都是公开 CDN，去掉尺寸后缀就能拿原图，不需要 API。但**必须转存到自己图床**，直链引用会被平台判定外链、且随时失效。 |

**一句话结论**：铺货软件 = 「逆向采集器 + 归一化数据模型 + 类目属性映射表 + 后台发布模拟器」。API 只是可选的合规外挂，不是核心。

### Q2：能不能做成一键铺货？

能。难点不在"抓"，在 **类目属性映射**。抓数据是 3 天工作量，把 A 平台的类目/属性/规格准确映射到 B 平台的类目/属性/规格，是这个项目 70% 的工程量，也是所有铺货软件"上架失败率高"的真正原因。

链路固定为四段，任何铺货软件都是这四段：

```
[采集] → [归一化 Normalize] → [映射 Map] → [发布 Publish]
   ↓            ↓                  ↓            ↓
 原始JSON    统一 Item 模型      目标平台载荷   草稿/上架
```

### Q3：本仓库给了什么

- `docs/` —— 完整规划：方案对比、架构、**三平台接口与签名细节**、类目属性映射方法论、实施路线
- `schema/item.schema.json` —— 归一化商品模型（跨平台通用）
- `src/` —— 可运行骨架：mtop 签名客户端、淘宝/1688/京东采集器、图片下载转存、映射器、发布适配器、FastAPI 服务
- `run_demo.py` —— 命令行跑通"一个链接 → 一份完整商品包"

---

## 1. 快速开始

> **本机环境提示**：系统 PATH 里的 `python`（`C:\Users\hao\AppData\Local\Programs\Python\Python312`）标准库目录 `Lib\os.py` 缺失，除 `--version` 外任何调用都会报 `ModuleNotFoundError: No module named 'encodings'`。请用 harness 自带的解释器：
>
> ```powershell
> $py = "C:\Users\hao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
> & $py -m pip install -r requirements.txt
> ```
> 下文命令里的 `python` 都替换成 `& $py`。该解释器已装好 pydantic / httpx / PyYAML / Pillow / lxml / fastapi / uvicorn / json5。

### 1.1 先登录（淘宝 / 1688 必需）

```bash
python scripts/login.py --platform taobao     # 弹出浏览器，扫码登录后回车
python scripts/login.py --platform alibaba
```

登录态落在 `config/cookies/<platform>.json`，可用 `SessionHealth` 体检。**京东不登录也能跑一部分。**

### 1.2 采集

```bash
python run_demo.py --platform jd --id 100008348542          # 京东：现在就能跑
python run_demo.py --platform taobao --id <真实商品ID>        # 淘宝：需先登录
python run_demo.py --platform jd --batch links.txt --skip-existing --delay 1.5
# 产物：data/items/<item_id>.json  +  data/media/<item_id>/*.jpg
```

淘宝/1688 也可以手工把浏览器 Cookie 导出成 `config/cookies/taobao.txt`（Netscape 或 `k=v; k=v` 格式都支持）。

### 1.3 自检（离线，不联网）

```bash
python tests/test_smoke.py        # 29 项：签名 / 归一化 / 映射 / 清洗 / 发布载荷
python tests/test_collectors.py   # 35 项：用真实抓取的京东页面做夹具
python tests/test_session.py      # 51 项：cookie 解析 / 健康检查 / 风控识别
```

### 1.4 启动服务

```bash
python -m uvicorn src.api.main:app --reload --port 8000
# POST /collect  {"platform":"jd","item_id":"..."}
# POST /publish  {"item_id":"...","target":"taobao","shop":"myshop","mode":"draft"}
```

## 1.5 当前进度（M1 已完成，实测）

| 平台 | 状态 | 说明 |
|---|---|---|
| 京东 | ✅ 可跑 | 移动端 SSR + 内嵌 JSON；价格/详情图需登录 |
| 淘宝/天猫 | ✅ 可跑（需登录） | 标题/价格/主图/**视频**/SKU/参数全通；详情图靠 `--render-desc` |
| 1688 | 🟡 代码就绪，待登录 | 页面被 x5sec 拦截；API 名需登录后抓包确定 |

**单条商品能拿到**：标题、价格区间、主图、**主图视频（mp4 + 封面 + 画幅比）**、详情图、
销售属性、SKU（含价格库存）、参数表、店铺、品牌。

**采不到**：淘宝商品类目 ID（`pcdetail` 接口不返回该字段）、京东价格与详情图（需登录）。

详细实测数据见 [docs/06-M1采集实测报告.md](docs/06-M1采集实测报告.md)。

## 1.6 请求限速（硬性）

所有出站请求都过 `src/collect/ratelimit.py`，三层保护：

| 层 | 规则 | 触发后 |
|---|---|---|
| 最小间隔 | 淘宝 3s、1688 4s、京东 1.5s | 阻塞等待 |
| 每小时配额 | 淘宝 100、1688 80、京东 400 | 阻塞到配额释放 |
| 熔断冷却 | 滑块 30min、风控 15min、反爬 20min | **直接拒绝，不发请求** |

冷却状态持久化在 `data/app.db`，**跨进程生效**——一次触发，所有脚本都停。

```bash
python scripts/rate_status.py                  # 查看配额与冷却
python scripts/rate_status.py --reset taobao   # 确认风控解除后手动解封
```

批量采集务必加间隔：`--delay 3`。

---

## 2. 目录导航

| 文档 | 内容 |
|---|---|
| [docs/01-方案对比与原理.md](docs/01-方案对比与原理.md) | 三种技术路线、为什么开放平台 API 干不了这活、各平台反爬机制 |
| [docs/02-架构与数据流.md](docs/02-架构与数据流.md) | 分层架构、模块职责、任务队列、存储设计、数据流时序 |
| [docs/03-采集接口与签名.md](docs/03-采集接口与签名.md) | **mtop sign 算法、`_m_h5_tk` 令牌机制、三平台具体接口与返回结构、图片 URL 规则、京东价格/库存/详情接口** |
| [docs/04-映射与发布.md](docs/04-映射与发布.md) | 类目映射、属性映射、SKU 笛卡尔积重建、发布载荷构造、图片空间上传 |
| [docs/05-实施路线.md](docs/05-实施路线.md) | 4 个里程碑、工期估算、验收标准、工程约束清单 |
| [docs/06-M1采集实测报告.md](docs/06-M1采集实测报告.md) | **三平台真实接口存活状态、能力矩阵、登录态要求、复现脚本** |

## 3. 工期估算（单人）

| 里程碑 | 内容 | 工期 |
|---|---|---|
| M1 | 单平台采集 + 归一化 + 图片转存（命令行可跑） | 5–7 天 |
| M2 | 类目/属性映射表 + 单平台发布到草稿箱 | 10–15 天 |
| M3 | 批量任务队列 + Web 管理后台 + 多店铺 | 10 天 |
| M4 | 多平台互铺（1688→淘宝、淘宝→京东）+ 失败重试与告警 | 10–15 天 |

合计 **6–8 周**做出可用版本，其中 M2 是决定成败的一段。
