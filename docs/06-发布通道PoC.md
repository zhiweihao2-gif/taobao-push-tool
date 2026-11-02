# 发布通道 PoC —— CDP 挂接浏览器

> 结论先行：**没有走抓包。** 全网没有公开的「抓包直调淘宝发布接口」成功案例，
> 能跑通的全部是浏览器自动化。原因不是技术上做不到，是**投入产出比不成立**。

## 一、为什么放弃抓包

调查方式：GitHub 仓库搜索 5 个关键词 + 代码搜索 3 个精确关键词（命中 372 个文件）+ 通读 6 个仓库的 README 与源码。

| 仓库 | 星 | 路线 | 是否真跑通 |
|---|---|---|---|
| `glide-the/taobao-agent` | 1★ | Playwright/CDP | **是**，2026-08-19 实测发布成功，100/100 分 |
| `ADChahaha/merch-flow` | 1★ | CDP 挂 Chrome 9222 | 是，来自实战经验 |
| `victorytianyi-dev/taobao-shop-automation` | 9★ | AI agent + 浏览器 | **是**，5 个商品全部上架 |
| `chengyu2727/ecommerce-listing-automation` | 3★ | 影刀 RPA | 是，四平台 |
| `automation9417/qianniu` | 77★ | clicknium 桌面自动化 | 是 |

**零个**走抓包。`chengyu2727` 的 README 把四条路摆在一起对比后直接写：

> - 平台开放平台 API：❌ 需要企业资质与开发者审核，个人拿不到
> - 商家工作台（生意参谋、京麦）：❌ 它们没有「发布商品」的开放接口
> - 平台自带批量导入：❌ 字段各平台不同、部分类目不支持

四条技术原因，一层比一层致命：

1. **发布页是 CSR + 动态签名**。提交请求带 `_tb_token_`、`x5sec`、设备指纹、时间戳签名，每次现算。
2. **重放容易被判**。淘宝校验请求上下文（UA 指纹、鼠标轨迹、页面停留、Referer 链路）。`httpx.post` 的特征和真实浏览器差太远。
3. **发布是低频动作**。一天 5 个商品，UI 自动化 90 秒/个 = 7.5 分钟。为省 7 分钟去逆向会变的接口，ROI 是负的。
4. **收益不对称**。UI 自动化 100% 能成，改版了改 selector；抓包可能白干，改版了全废。

## 二、采用方案：CDP 挂接你自己的浏览器

```
┌─────────────────────────────────────┐
│ 你自己的 Edge/Chrome（手动启动）      │
│   --remote-debugging-port=9222      │
│   你扫码登录卖家中心（一次，长期有效） │
└──────────────┬──────────────────────┘
               │ CDP (localhost)
               ▼
┌─────────────────────────────────────┐
│ 本工具（Python + Playwright）         │
│   复用你的 cookie / 指纹 / 登录态     │
│   填表 → 截图 → 停下等人工确认        │
└─────────────────────────────────────┘
```

**五条设计原则**（每条都来自别人踩过的坑）：

| 原则 | 原因 |
|---|---|
| **绝不自己启动浏览器** | 浏览器被我们重启过，登录态和指纹就变了，平台当成新设备 |
| **登录永远由真人完成** | 不碰扫码/滑块/短信/人脸，这些是风控的正面战场 |
| **标题/价格/库存必须 `keyboard.type`** | 淘宝输入框是受控组件，`fill()` 或直接赋 value 在提交时会被清空 |
| **品牌/型号用原生 setter + input/change** | React 劫持了 `value` setter，直接赋值不更新组件内部状态 |
| **直连类目 URL，不点类目树** | 类目树是多级联动，点错一级就进错类目 |

## 三、文件

| 文件 | 作用 |
|---|---|
| `src/publish/browser_pub.py` | 底座：CDP 连接、登录检测、页面探测、输入原语 |
| `scripts/publish_poc.py` | CLI：`check` / `pages` / `probe` / `fill` |
| `scripts/start_chrome_debug.py` | 启动带调试端口的独立浏览器 |
| `10-发布PoC.bat` | 交互菜单启动器 |

## 四、使用流程

### 第 1 步：启动带调试端口的浏览器

双击 `10-发布PoC.bat` 选 1，或直接跑：

```
python scripts/start_chrome_debug.py
```

脚本会自动找 Chrome / Edge，用独立 profile（`%LOCALAPPDATA%\dsh-chrome-debug`）启动，不干扰你日常用的那个。

> **为什么必须独立 profile**：Chrome 只允许「首次用某个 user-data-dir 启动时」打开调试端口。给日常 profile 加参数，会因为已有实例而忽略。

### 第 2 步：扫码登录

在弹出的浏览器里打开 https://myseller.taobao.com/home.htm 扫码登录。

**这一步只能真人做。** 登录态通常能保持数天到数周。

### 第 3 步：检查

```
python scripts/publish_poc.py check
```

输出会告诉你：端口是否通、浏览器版本、有几个标签页、登录态是否有效。

### 第 4 步：探测发布页结构

```
python scripts/publish_poc.py probe --cid 50023905
```

`--cid` 是目标类目 ID（交换机是 50023905）。脚本会：

1. 访问卖家中心确认登录
2. 打开发布页（不带 catId，看落地到哪）
3. 直连带 catId 的发布页
4. **dump 出所有可填字段**（selector / label / placeholder / 是否可见）
5. 按用途归类（title / price / stock / brand / model）
6. 截图

产物：`data/publish_poc/probe_50023905.json` + `probe_50023905.png`

### 第 5 步：填表（当前版本到截图为止）

```
python scripts/publish_poc.py fill --item 1073025553052 --cid 50023905
```

打开页面、dump 字段、截图。**不会点提交。**

## 五、已知 selector（来自 `glide-the/taobao-agent` 的实证记录）

这些是 2026-08-19 有人真跑通时记下的，可作为探测结果的对照：

```
类目入口:  https://item.upload.taobao.com/sell/ai/category.htm
编辑路由:  /sell/v2/publish.htm
成功路由:  /sell/v2/success.htm          ← 提交成功的唯一可靠标志
主图槽位:  #sell-field-images .empty-container
图片空间:  iframe[src*="sucai.wangpu.taobao.com/select.htm"]
空间内素材: .item.pic .cover.list-item
裁剪保存:  .btn.btn-blue
已上传图:  .sell-o-image-item.has-url img.image-item
接受格式:  jpg,bmp,gif,heic,png,jpeg
```

同一份记录里的踩坑：

- 点主图槽位后**不会弹系统文件选择器**，弹的是云端图片空间 iframe
- 点已经放好的主图会打开预览，不是选择器
- 裁剪弹窗默认把 1500×1500 裁成居中 750×750，会切掉文字 —— 必须手动拖到图片边界
- 裁剪保存成功 ≠ 发布成功

## 六、提交成功的判定标准

不能只看「点了提交没报错」。要三层全过：

1. 页面跳到 `/sell/v2/success.htm` 且显示「商品提交成功」
2. 拿到分配的 item id 或商品链接
3. 打开线上商品页，确认标题、价格、主图、属性、物流条款都对

## 七、当前进度

- [x] CDP 连接层（Chrome / Edge 都支持）
- [x] 登录态检测
- [x] 发布页导航（裸页 + 带 catId）
- [x] 字段 dump + 自动归类
- [x] 截图
- [x] 输入原语（`human_type` / `set_value_native` / `click_text`）
- [ ] **填表逻辑**（等 probe 结果确认 selector）
- [ ] 主图上传（图片空间 iframe，需要坐标偏移）
- [ ] SKU 表填写（勾选规格 → 生成行 → 逐行填价/库存）
- [ ] 运费模板选择
- [ ] 提交前确认截图 → 人工放行
- [ ] 提交后错误逐项补

## 八、风险与边界

| 项 | 说明 |
|---|---|
| 账号风险 | 平台对「非人类操作特征」在发布场景更敏感（涉及资金/库存）。本工具用真人登录态 + 真人节奏（30~90 秒随机间隔），但不保证不被识别 |
| 平台改版 | 发布页 DOM 一变，selector 就失效。需要重新 probe |
| 图片版权 | **这是铺货最现实的翻车点**。复制别人主图/详情图被投诉会判「盗图」，扣分甚至下架。裁水印不改变侵权性质 |
| 自动化程度 | 上架流程多数自动，登录/验证码/审核必须真人 |
| 频率 | 建议单日不超过 10~20 个，间隔随机 |

## 九、参考

- `glide-the/taobao-agent` — `skills/taobao-shop-publish-product/references/verified-browser-patterns.md`
- `ADChahaha/merch-flow` — `backend/app/agent/publish.py`（CDP 实战守则 10 条）
- `chengyu2727/ecommerce-listing-automation` — README「两条边界」「执行层」「局限与边界」
- `victorytianyi-dev/taobao-shop-automation` — AGENTS.md 契约 + `02-自动化程度说明.md`
