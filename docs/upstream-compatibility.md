# sub2api 接口兼容性

首次审阅记录为 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) 的 `0.2.22`，正式标签 `v0.2.22` 的提交 `b0fa9f3e6eeab1968a53e1289b69daa4bb40ca2b`，检查日期为 2026-11-17。当前版本的精确基线以 [upstream-baseline.json](upstream-baseline.json) 及本次 Release 证据为准。源代码审阅和本地契约测试不等于已在生产实例验证；更新后的接口必须重新检查。

## 已使用的接口

管理请求使用 `x-api-key`，普通用户请求使用 `Authorization: Bearer <JWT>`。模型请求使用普通用户 API Key；管理员 API Key 不能用来代替模型密钥。成功响应要求 `code: 0` 与 `data`。列表响应要求 `data.items`、`total`、`page`，助手完整读取所有页，检测重复对象、数量变化和提前结束，避免悄悄遗漏对象。

原生列表增强依赖 `frontend/src/components/common/DataTable.vue` 的 `tr[data-row-id]`，以及 `views/admin/UsersView.vue`、`AccountsView.vue` 的 `row-key="id"` 和操作单元格。脚本只在 `/admin/users` 与 `/admin/accounts` 插入按钮，点击时重新读取当前行的数字 ID，兼容虚拟列表复用行节点。移动端没有可靠 ID 时不添加操作，不按姓名或顺序推断。上游目前没有可用的全局自定义脚本或行扩展接口，所以通过同源网页代理注入外部脚本与样式，保留上游 CSP，不修改官方镜像。

默认接入只将 `/_helper/` 交给 Helper。原站 API、模型网关、SSE、WebSocket 及静态文件继续由入口直接转发到 sub2api。试点开启注入后，Helper 只接收网页导航 GET，核实返回内容是 SPA 入口后才注入脚本。网页代理独立于管理 API 客户端，使用配置的公网地址重写同源重定向，管理 Key 仅用于 Helper 自身的管理请求。原生身份验证要求 `session_binding_enabled` 明确为 `false`，并逐次核实启用中的管理员身份。

| 功能 | 方法及路径 | 关键字段 |
| --- | --- | --- |
| 账号列表 | `GET /api/v1/admin/accounts?lite=true` | `id,name,status,schedulable,platform` |
| 调度开关 | `POST /api/v1/admin/accounts/:id/schedulable` | `{"schedulable":true/false}` |
| 用户列表/详情 | `GET /api/v1/admin/users`、`GET /api/v1/admin/users/:id` | `id,email,role,status,created_at,last_used_at` |
| 禁用/恢复用户 | `PUT /api/v1/admin/users/:id` | `{"status":"disabled"}` 或 `active` |
| 读取设置 | `GET /api/v1/admin/settings` | `custom_menu_items,session_binding_enabled` |
| 更新菜单 | `PUT /api/v1/admin/settings` | 仅 `custom_menu_items` |
| 用户身份 | `GET /api/v1/user/profile` | 使用用户 JWT，验证真实 `id` 和 `role` |
| 作品画廊分组可见性 | `GET /api/v1/groups/available` | 使用当前用户 JWT，取有效分组 `id`，详情也重新验证 |
| 管理模型统计 | `GET /api/v1/admin/dashboard/models` | `data.models` |
| 本人模型统计 | `GET /api/v1/usage/dashboard/models` | 使用用户 JWT，仅本人数据 |
| 用量排名 | `GET /api/v1/admin/dashboard/users-ranking` | `data.ranking`、总成本、请求数与 token 数 |
| 默认模型单价 | `GET /api/v1/admin/channels/model-pricing?model=...` | `found,input_price,output_price,cache_write_price,cache_read_price` |
| 版本 | `GET /api/v1/admin/system/version` | `data.version` |
| 模型检测 | `POST /v1/chat/completions` | 使用普通用户 API Key，OpenAI 兼容协议 |
| 管理员归属 | `GET /api/v1/admin/compliance` | `acknowledgement.admin_user_id` 是当前管理凭证对应身份 |
| 管理员模型密钥 | `GET /api/v1/admin/users/:id/api-keys` | 分页，包含原始 `key`、`user_id`、`group_id`、状态与额度，只在服务端使用 |
| 分组列表/详情 | `GET /api/v1/admin/groups`、`GET /api/v1/admin/groups/:id` | 完整分页；`rate_multiplier` 和日/周/月额度上限 |
| 有效订阅列表/详情 | `GET /api/v1/admin/subscriptions?status=active&group_id=...`、`GET /api/v1/admin/subscriptions/:id` | `user_id,group_id,starts_at,expires_at,status` 和窗口用量 |
| 订阅窗口进度 | `GET /api/v1/admin/subscriptions/:id/progress` | `daily/weekly/monthly`，含 `limit_usd,remaining_usd,resets_at` |
| 用户专属分组倍率 | `PUT /api/v1/admin/users/:id` | `{"group_rates":{"<group_id>":0.5}}`；单项 `null` 清除 |
| 可靠专属倍率读取 | `GET /api/v1/admin/groups/:id/rate-multipliers` | `user_id,rate_multiplier`，错误不会被吞掉 |
| 分组默认倍率 | `PUT /api/v1/admin/groups/:id` | 仅 `{"rate_multiplier":0.5}`，影响全组默认计费 |
| 完整用户月消费 | `GET /api/v1/admin/usage/stats?user_id=...` | 显式月起止日期、时区、`nocache=true`；`total_actual_cost,total_requests,total_tokens` |
| 余额奖励 | `POST /api/v1/admin/users/:id/balance` | `balance` 为增加额、`operation:add`、固定 `notes` 和 `Idempotency-Key` |

统计日期为 `start_date`、`end_date`，格式 `YYYY-MM-DD`，结束日期含当天。显式传递 `timezone=Asia/Shanghai`。模型统计的 `cost` 为标准计费，`actual_cost` 为实际扣除，`account_cost` 为管理端成本；普通用户页面不应暴露管理端成本。默认模型定价为每 token，换算每百万 token 需乘以一百万，不能直接将累计消费称为单价。

## 必须保留的行为与限制

- 调度只修改账号 `schedulable`，不恢复错误状态、刷新凭证或覆盖账号 `status`。适用于每天跨午夜的免费额度时段。
- 用户 `last_used_at` 是实际模型使用时间，`last_active_at` 是面板访问活动。上游 `admin_user.go` 在查询最后使用时间失败时可能只写日志、返回空值，因此空值不应自动当作长期未使用。自动禁用应排除管理员并在执行前重新读取用户状态和最后使用时间。
- 上游没有发送自定义主题和正文的管理邮件接口。`/admin/settings/send-test-email` 使用写死的测试内容；读取 SMTP 设置只返回密码是否已配置。助手需要独立 SMTP 配置，不能凭管理 Key 取得上游 SMTP 密码。
- 智商检测作品画廊通过管理员凭证查找其所属账号已有的普通模型密钥，调用配置绑定的确切分组。每轮生成前重新验证密钥有效性，用量和费用记在所选模型 Key 上。浏览器只接收密钥名称和 ID，原始密钥只用于服务端请求。普通用户的画廊列表和作品详情均以当前 JWT 的可用分组为准。固定题检测后端保留原有调用方式与历史摘要，但「智商检测」管理页不再提供固定题自检入口。
- 内嵌普通用户通过本人 JWT 读取 `/api/v1/keys`，只能选择自己已有有效密钥绑定的分组，不能借用管理员密钥消费。
- 当前设置更新实现有 `omittedSettingKeys(sentFields)`：省略字段保持原值，因此菜单提交只写 `custom_menu_items`。更旧版本可能是整文档替换，助手不得声称任意旧版安全兼容。
- 菜单列表最多 20 项，ID 最多 32 字符（字母、数字、`-`、`_`），`visibility` 为 `user` 或 `admin`。助手只管理固定前缀的菜单 ID，保留其他菜单。上游没有 ETag 条件写入，两个管理员同时修改菜单存在最后写入覆盖的竞争窗口。
- 上游 iframe URL 会自动附带 `token`（用户 JWT）、`user_id`、主题和语言等参数。助手必须通过用户 profile 验证，不能信任 URL 中的用户 ID。立即移除 URL 敏感参数，不在日志、页面外链或 Referer 中传播它们；反向代理也应对查询参数脱敏。
- 上游启用 `session_binding_enabled` 后，服务器代理校验用户 JWT 若改变 IP 或 User-Agent，可能撤销原会话。未配置可信代理转发前应阻止这种校验并给出提示，不能随意信任客户端传来的 X-Forwarded-For。
- 管理员 Key 不应发送给浏览器、用户接口、模型接口或重定向目的地。适配器关闭重定向并去除注入 HTTP 客户端的默认认证头和 Cookie。
- `adm_sc_` 受限 Key 可授予 `accounts.read`、`accounts.status`、`users.read`、`dashboard.read`、`system.read`。用户禁用、菜单设置、模型定价目前缺少对应细粒度权限，需要旧管理 Key 或 FullAccess 管理 Key。
- 上游可能返回 HTTP 423、`ADMIN_COMPLIANCE_ACK_REQUIRED`。需管理员在 sub2api 自己的界面完成部署与运营确认，助手不代确认。

## 订阅倍率规则

订阅余额与钱包 `balance` 是不同数据。上游只提供日、周、月额度窗口，不提供统一的“订阅整个生命周期剩余额度百分比”。规则必须明确选择 `daily`、`weekly` 或 `monthly`；剩余比例使用 `remaining_usd / limit_usd`，接口的 `percentage` 字段反而是**已用比例**。

`expires_in_days` 向上取整，因此“小于 1/2 天”应比较实际 `expires_at` 与当前时间差。订阅详情含 `starts_at`、`expires_at`、`status`，必须重新确认用户、分组、订阅仍有效，防止根据旧列表对已续费、撤销或过期对象操作。`GET progress` 不会初始化或重置窗口；额度窗口缺失、未激活、上限不限额、数值无效或 `resets_at` 已过去时，不能据此触发优惠。适配器读取订阅详情时另取最新分组，避免沿用已修改的旧套餐上限。

个人分组倍率是一个**绝对覆盖值**，优先于分组默认倍率，不是与默认值相乘。设置 `0.5` 表示该用户在这个分组使用 `0.5` 倍率。非空 `group_rates` 仅修改传入的分组；单项 `null` 清除这一分组的覆盖值，空对象 `{}` 会清除该用户所有专属倍率。因此助手始终只发送一个非空键值映射。

不要将 `PUT /groups/:id/rate-multipliers` 用作单用户修改：该接口替换整个分组的用户专属倍率集合。`PUT /groups/:id` 仅写 `rate_multiplier` 是安全的窄更新，但它会影响所有继承分组默认倍率的用户；已有个人覆盖值仍然优先。

上游 `UpdateUser` 对部分专属倍率写入错误仅记录日志、仍返回成功；`get_user.group_rates` 的读取错误也可能被忽略。因此助手通过组侧 `GET /groups/:id/rate-multipliers` 读取原值并验证写入/恢复，失败时不能当作“原来没有覆盖值”。倍率必须为有限正数，零值不被上游接受。上游没有用于倍率更新的原子 compare-and-set 接口，助手的写前重读和写后验证不能消除外部管理员在中间同时修改的竞争窗口。

读取订阅需要 `subscriptions.read`；分组和用户资料有对应读取权限。个人倍率修改与全组默认倍率修改目前需要旧管理 Key 或 FullAccess 管理 Key，受限 Key 的 `groups.models.write` 只允许模型字段，不能拿来修改计费倍率。

## 月度奖励与不能重复入账的边界

上游 `dashboard/users-ranking` 将 `limit` 强制限制为 50，也没有分页；它按 `actual_cost DESC, tokens DESC, user_id ASC` 排序。月度奖励不能将这个接口称为“完整普通用户排行榜”。助手完整分页读取当前用户后，对每个有效普通用户调用带月起止日期、时区的 `usage/stats` 汇总，并以实际扣除 `total_actual_cost` 生成完整候选列表，最多四个并发请求；缺失资格字段或任何汇总失败会中止整个快照。该方式每次结算需要约 N 个读取请求；不需要逐条下载可能很大的用量明细。已删除、禁用、管理员和零消费用户不属于当前候选。最终资格仍需在实际发奖前重新检查，最终同消费排序由助手规则统一确定。

奖励使用 `operation:add`，不读取旧余额后使用 `set`，避免覆盖同时发生的真实计费扣款。`balance` 是增加额，`notes` 只由固定奖励标识构造；同一奖励的 payload、用户、金额和幂等 Key 必须保持不变。管理员加余额可能触发上游的 `admin_recharge_rebate_enabled` 邀请返利设置；当前接口没有单次禁止返利的参数。

**上游 Idempotency-Key 不能单独保证跨崩溃永不重复入账。** 默认写入幂等记录只保留 24 小时（可由上游配置改变），且余额原子增加与幂等成功记录是不同事务。余额增加后，获取用户、生成流水、保存幂等响应等步骤仍可能失败或进程崩溃。过期记录可以被重新执行，某些失败状态也允许再次执行；轮换管理身份或受限 Key 权限还可能改变请求指纹。旧 Key 对应 `admin:<id>`，受限 Key 对应 `admin-key:<keyid>:<policyhash>`。

因此自动发奖必须先在助手持久化不可变月结快照及每笔唯一意图，再发送**一次**入账请求，成功后记录结果。超时、网络异常、非成功响应或助手在请求前后崩溃导致结果不明时，不得因为同一个幂等 Key 而自动重试；应保留“待人工核对”状态。上游余额流水 notes 的出现可辅助证明已发奖，但流水缺失不能证明未入账，因为上游记录流水失败可能被忽略。成功/不明奖励的永久本地台账不能随规则删除、修改奖励金额或重启而清除。适配器不实施任何自动重试。

上游读取全站用量需要 `usage.read`，增加余额需要 `users.balance.write`。即使权限允许，助手也应保持普通用户资格、正金额和完整榜单快照检查。只有余额接口的成功状态并不授予读取用户余额/资料的额外权限。

## 上游更新检查

接口实现集中在 `helper/upstream.py`。`tests/test_upstream.py` 使用与上游相同的响应包络验证分页、状态修改、日期参数、认证隔离、异常脱敏和重定向隔离。

上游源代码中的检查点：

- `backend/internal/server/routes/admin.go`、`routes/user.go`
- `backend/internal/server/middleware/admin_auth.go`、`jwt_auth.go`、`session_binding.go`
- `backend/internal/handler/admin/account_handler.go`、`user_handler.go`、`setting_handler_update.go`、`setting_handler_email.go`
- `backend/internal/handler/dto/settings.go`、`types.go`
- `backend/internal/pkg/usagestats/usage_log_types.go`
- `frontend/src/utils/embedded-url.ts`

持续集成应定期读取上游 main/release，比较以上路径和字段；发现变更时要求人工审阅并运行本地测试。部署前还应使用测试实例执行只读能力检查；修改账号、禁用用户、更新菜单的真实联调必须在授权的测试对象上进行。静态路径匹配只能发现部分变化，不能证明业务语义兼容。


## 正式版本基线

最初审阅的 main 提交为 `15871f904056f548dd874a1d069ffc6cdf777b78`。与官方 `v0.2.22` 标签作完整树比较后，只有 `backend/cmd/server/VERSION` 不同，其他 26 项受检查文件及 UI 源保持一致，因此已将基线固定到官方 tag。该 tag 中嵌入的 VERSION 为 `0.2.21`；正式 Release 的 tag 才是 Helper 跟版的版本权威，不能仅凭上游源码内版本文本判定发布号。新的发布流程见 [自动跟版与发布](releases.md)。
