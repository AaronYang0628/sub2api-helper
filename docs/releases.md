# 自动跟版与发布

Helper 的发布版本使用官方 `Wei-Shaw/sub2api` 正式 Release 的版本号，不再独立递增。
例如官方 `v0.2.23` 对应 Helper `helper/VERSION=0.2.23`、Git tag `v0.2.23`、
镜像 `ghcr.io/<owner>/sub2api-helper:0.2.23` 和 `:v0.2.23`。没有额外 `latest` 浮动别名，
部署时固定版本。上游源码中的 VERSION 可能滞后于正式 tag，以正式 Release tag 为准。

## 自动流程

1. `upstream.yaml` 每小时第 17 分钟（UTC）检查官方已发布的稳定版，也可手动运行。
   分页读取全部 Release，排除草稿、预发布和非法版本标签。按版本升序处理尚未同步的正式版，
   不只检查 latest，避免轮询期间发布多个版本时跳过中间版本。
2. 同时保留一个同步 PR。当前版本尚未发布完成时不推进后续版本；PR 被关闭未合并时暂停队列，
   请重新打开原 PR 继续处理，而不是反复创建重复 PR。首次运行也会为当前已对齐的正式版建立 PR 和发布证据。
3. 检出官方精确 tag，验证其提交，比较与已审阅基线之间的**全部文件变化**。除已有哈希清单，
   新增、删除、依赖、工作流及其他未分类的变化也会被检查。已知文档及上游版本文本变化归为非运行时变化。
4. 无相关变化也创建 `automation/upstream-vX.Y.Z` PR，更新版本、当前使用示例和兼容记录；
   需要适配时创建草稿 PR，列出相关变化并阻止未审阅版本发布。不会自动声称完成语义适配。
5. 机器人创建 PR 后显式 dispatch CI。不能依赖 `GITHUB_TOKEN` 创建 PR 后自动触发 PR 事件。
   CI 验证统一版本、接口及规则测试、UI 构建、容器流程，以及固定上游 SHA 的兼容证据。
6. 人工合并 PR、默认分支的 push CI 成功后，`image.yaml` 通过 `workflow_run` 接续发布。
   它重新验证确切测试 SHA、同仓库已合并同步 PR、官方 Release 身份、批准指纹及不可变 tag。
   普通代码提交、PR CI、分支 dispatch 和 fork 的运行不会触发发布。
7. 同一发布流程先保留不可变 Git tag 作为发布意图，再构建 amd64/arm64 镜像，最后创建 GitHub Release。
   不依赖机器人创建 tag 后触发另一个工作流，避免事件被 GitHub 抑制而中断。

版本源只有 `helper/VERSION`；运行时通过包资源读取，Docker 也复制同一资源。
`scripts/version.py` 同步 Compose、UI 包自身元数据和 README 当前示例，保留依赖版本与历史记录。
CI 使用 `--check` 防止任一处版本漂移。

## 首次启用

将这些代码推送至仓库默认分支，在仓库设置启用 Actions，并允许工作流创建 PR：

- Settings → Actions → General → Workflow permissions，启用 **Allow GitHub Actions to create and approve pull requests**。
- 工作流分别声明所需的 `contents`、`pull-requests`、`actions`、`packages` 权限；组织策略也需要允许这些权限。
- 推荐分支保护要求 `Test and build / test` 通过，并保留 PR 人工审阅。
- 首次可在 Actions 中手动运行 **Sync upstream releases**。默认使用内置 `GITHUB_TOKEN`，不需要管理员 API Key、SMTP 或额外 PAT。

尚未推送到默认分支、权限未启用，或 GitHub Actions 尚未实际执行时，不能将本地测试称为已在线建 PR/发布。
本地云环境的 GitHub API 出站权限与 GitHub Actions runner 的权限是不同的。

## 有相关变化时如何适配

PR 的 `docs/upstream-release.json` 记录官方 Release ID、发布时间、精确 SHA、旧基线、全部差异及证据指纹。
先调整受影响的适配器、规则或 UI，并验证接口和实际测试实例。复制 UI 源时保留上游许可和署名，重新生成静态 CSS。

在仓库外准备精确 tag 的上游 checkout，并取得 PR 中该 Release 的真实 JSON：

```sh
gh api repos/Wei-Shaw/sub2api/releases/tags/vX.Y.Z > /tmp/official-release.json
git clone --depth 1 --branch vX.Y.Z https://github.com/Wei-Shaw/sub2api.git /tmp/sub2api-exact-release
# 旧基线的提交树也必须可读；PREVIOUS_SHA 取 PR 清单中的 previous_baseline.reviewed_commit
git -C /tmp/sub2api-exact-release fetch --depth 1 --no-tags origin "$PREVIOUS_SHA"
python3 scripts/sync_upstream_release.py approve \
  --release-json /tmp/official-release.json --source /tmp/sub2api-exact-release
python3 scripts/version.py --check --tag vX.Y.Z
python3 -m unittest discover -s tests -v
```

`approve` 只批准本次精确 SHA 和证据指纹，并写入候选基线与批准文件。新版本不能沿用旧批准，
修改发布时间、目标 tag 或相关文件会使批准失效。将适配和批准一起提交到该 PR，CI 通过后标记 Ready for review 并合并。
无相关运行时变化时，候选基线由机器人写入 PR，仍需通过 PR 合并才进入默认分支。

目前自动队列跟踪稳定 `MAJOR.MINOR.PATCH` 的向上版本序列；较低维护线的回补版本需要使用维护分支另行发布，
不能将默认分支版本倒退。预发布版本不进入正式发布队列。

## 发布失败如何恢复

在 **Publish aligned release** 手动运行，填写该同步 PR 合并后的确切 SHA。仍必须有该 SHA 的成功默认分支 push CI。
每个 SHA 有独立并发组，避免 GitHub 的“只保留一个 pending run”规则丢弃中间版本；版本队列要等当前 GitHub Release 完成后才继续。

- 已有 tag 只能指向同一 SHA，不能重打旧版本。
- 已有镜像必须带匹配的版本、提交与两个平台清单；同版本别名必须指向同一 digest。
- 只缺镜像别名时，从已验证的不可变 digest 恢复，不能覆盖不一致的既有别名。
- 镜像存在且正确时复用；GitHub Release 最后发布，并记录 digest、平台、上游提交、Helper 提交及兼容证据。
- 已存在的 Release 必须与受管理的证据标记一致，重跑会保留人工补充的发布说明。

本地离线测试使用可控的 Git checkout、GitHub API 和镜像清单替身，覆盖无变化发 PR、需批准变化、
分页队列、版本一致性、权限来源、失败恢复和标签冲突。实际 GitHub 权限、跨架构构建与远端发布仍由仓库内的工作流执行验证。
