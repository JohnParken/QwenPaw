# 发布 QwenPaw

_English: [RELEASING.md](RELEASING.md)_

QwenPaw 的一个版本发布三种产物：**PyPI** wheel、**Web Docker** 镜像和
**插件**包。统一发布工作流会先构建并验证全部产物，再发布 GitHub 草稿 Release。

> 编排器：[`.github/workflows/release.yml`](.github/workflows/release.yml)。

## 发布步骤

1. 创建包含目标 tag 和发布说明的 GitHub 草稿 Release，不要手动发布。
2. 在 GitHub Actions 运行 **Release (unified)**。`tag` 留空时会选择唯一草稿，
   也可以显式填写。真实发布时不要启用 `dry_run`。
3. 工作流把草稿目标解析为固定 commit SHA，构建 wheel、验证 Web 服务与镜像定义，
   并打包插件。
4. 所有准备任务通过后，工作流发布 PyPI、多架构 Web 镜像和插件，随后将草稿改为
   已发布状态；正式版/post 还会部署官网并创建 Release Duty issue。

发布 tag 必须与 `src/qwenpaw/__version__.py` 经 Python packaging 版本归一化后的结果
一致。例如，`v2.0.1-beta.1` 对应 `2.0.1b1`。

## 版本类型

| 类型 | tag 示例 | 草稿勾选预发布 | Docker 标签 |
|------|----------|----------------|-------------|
| beta / rc / alpha / dev | `v2.0.0-beta.8` | 是 | `<version>` + `pre` |
| 正式版 | `v2.0.0` | 否 | `<version>` + `pre` + `latest` |
| post | `v2.0.0.post4` | 否 | `<version>` + `pre` + `latest` |

公开官网只在正式版和 post 版本发布时部署。

## 故障处理

- 准备任务失败时不会发布任何产物，草稿保持不变。修复后重跑失败任务或整个工作流。
- 某个发布任务在其他产物已发布后失败时，重跑失败任务即可；Docker 推送是幂等的。
  如果 PyPI 版本无法复用，请发布 `.postN` 版本。
- `finalize` 失败时，重跑该任务，或执行
  `gh release edit <tag> --draft=false --target <sha>` 手动发布草稿。
- 官网部署或 Release Duty issue 失败时，重跑对应任务；这不会改变已发布的产物。
- 草稿识别有歧义时，使用显式 `tag` 重跑。

## 旧流程回退与 dry run

统一工作流不可用时，可以发布 GitHub Release，以触发保留的 `publish-pypi`、
`docker-release` 和 `plugins-release` 三个独立工作流。

运行 **Release (unified)** 并设置 `dry_run: true`，可验证版本解析、构建、检查、门禁
和草稿发布流程，同时跳过 PyPI、Docker 与插件产物上传。
