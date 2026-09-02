# 雪球（Xueqiu）博主可爬性调研结论

> 调研日期：2026-09-02 · 状态：**结论已定，实施搁置**
> 用户决策（AskUserQuestion 2026-09-02）：登录方案=先搁置；接入范围=之后再做；博主名单=之后用户提供。

## 一句话结论

**技术上能爬**（Playwright 解阿里云 WAF + 浏览器内调 v4 timeline API，字段比头条更全），但**匿名会话限流极严**，撑不起简报每日 5 档的抓取量，**需要登录态 cookie** 才能可持续。登录方案用户已决定先搁置。

## 已验证证据（Playwright chromium，真实浏览器）

| 环节 | 端点 | 结果 |
|---|---|---|
| WAF 反爬 | `https://xueqiu.com/` 首页加载 | 纯 curl 被 `aliyun_waf_*` JS 挑战拦截；Playwright 加载首页 → 拿到 `xq_a_token`/`xqat`/`acw_tc` 等全套 cookie，WAF 解除 |
| 用户帖子列表 | `GET https://xueqiu.com/v4/statuses/user_timeline.json?user_id={uid}&page=N` | 200 JSON `{count:20, statuses, total, page, maxPage}`，分页游标正常；v5 同名接口已 404 |
| 纯原创帖 | `GET https://xueqiu.com/statuses/original/timeline.json?user_id={uid}&page=1&count=10` | 只返回原创帖（无转发/回复），更贴合"博主观点"；大 V（复极斋 maxPage=499）可翻 400+ 页 |
| 字段覆盖 | — | `created_at`(毫秒时间戳) + `description`(HTML) + `reply_count`/`retweet_count`/`like_count`/`view_count` 四项互动——比头条更全 |
| 昵称→user_id | `GET https://xueqiu.com/query/v1/search/user.json?q={昵称}` | 直接返回 id + 粉丝数 + 帖数。例：唐史主任司马迁 → id=2054435398（36.3 万粉，3052 帖） |
| 画像资料 | 用户主页 `/u/{uid}` DOM + `statuses/original/show.json` | DOM 有 粉丝/关注/IP 属地/帖数；show.json 有 intro/自选股数 |
| 内容质量 | 复极斋/唐史主任司马迁 等 | 有真实大盘方向帖（带时间戳与互动数据），可供观点信号 |

`description` 是 HTML（`<a href>` 引用、`$股票$` 标签），需去标签清洗。

## ⚠️ 硬门槛：匿名限流

- 冷启动首次 API 调用 200 ✅，但同一会话内连调很快触发：
  - `400 {"error_description":"请登录雪球查看更多内容"}`
  - `400 {"error_description":"访问频率太快了，请休息下"}`
- **实测 3 秒间隔 × 15 次 → 失败 12 次**（约每 5 次成功 1 次）。
- 恢复性：等 30 秒可恢复 200（临时限流非封禁），但预算又迅速耗尽。
- 简报量级 = 30 博主 × 1~3 页 × 每日 5 档 → 匿名额度远不够。

> 报错文案字面即"请登录"，且雪球公开爬虫方案均依赖登录 cookie → **登录大概率可解除限流，此为推断，待登录后实测确认**。

## 集成方案骨架（实施时照做）

对标 `scripts/pipeline/scrape_toutiao.py`（同架构）：

1. **`scripts/pipeline/scrape_xueqiu.py`**：
   - Playwright 启动 → 加载首页解 WAF → 注入登录态 cookie（`xq_a_token` 等）。
   - 浏览器内 `fetch(v4 user_timeline, {credentials:'include'})`，按 `maxPage` 游标翻页到 since 时间窗。
   - 输出与头条**同构**的 `data/posts/<博主>.json`：`{post_id, content, publish_time, publish_date, url, digg_count, comment_count, read_count}`（映射：like→digg、reply→comment、view→read）。
   - 适配：`created_at` 毫秒→秒对齐 `--since`；`description` HTML 去标签。
2. **登录态获取**（登录方案恢复时）：用户提供账号 → 手动登录一次存 `context.json`（或直接给 cookie）→ 实测是否解除限流。
3. **博主接入**：用户提供名单（昵称/主页链接）→ `search/user.json` 解析 user_id → 配置入库。
4. **简报接入**（接入范围恢复时）：按当时决策——单独频道 or 并入现有体系。

## 待决事项（用户已搁置）

- [ ] 登录方案（提供账号 / 注册小号 / 不登录降频）
- [ ] 接入范围（单独频道 / 并入现有榜单 / 只写爬虫积累数据）
- [ ] 博主名单（用户提供）
