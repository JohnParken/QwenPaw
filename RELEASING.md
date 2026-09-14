# Releasing QwenPaw

_中文版：[RELEASING_zh.md](RELEASING_zh.md)_

QwenPaw publishes three artifacts from one version: the **PyPI** wheel, the
**Web Docker** image, and the **plugins** bundle. The unified release workflow
builds and verifies them before publishing the draft GitHub Release.

> Orchestrator: [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Release procedure

1. Create a draft GitHub Release with the target tag and release notes. Do not
   publish it manually.
2. Run **Release (unified)** from GitHub Actions. Leave `tag` empty to select
   the single draft, or provide it explicitly. Keep `dry_run` disabled for a
   real release.
3. The workflow resolves the draft target to a commit SHA, builds the wheel,
   verifies the Web service and image definition, and packages plugins.
4. When all prepare jobs pass, it publishes PyPI, the multi-architecture Web
   image, and plugins. It then marks the draft as published, deploys the website
   for stable/post releases, and creates the Release Duty issue.

The release tag must match `src/qwenpaw/__version__.py` after Python packaging
version normalization. For example, `v2.0.1-beta.1` matches `2.0.1b1`.

## Version types

| Type | Example tag | Draft pre-release | Docker tags |
|------|-------------|-------------------|-------------|
| beta / rc / alpha / dev | `v2.0.0-beta.8` | yes | `<version>` + `pre` |
| stable | `v2.0.0` | no | `<version>` + `pre` + `latest` |
| post | `v2.0.0.post4` | no | `<version>` + `pre` + `latest` |

The public website is deployed only for stable and post releases.

## Troubleshooting

- If a prepare job fails, no artifact is published and the draft remains
  unchanged. Fix the failure and rerun the failed jobs or the workflow.
- If a publish job fails after another artifact has already been published,
  rerun the failed jobs. Docker pushes are idempotent. If a PyPI version can no
  longer be reused, publish a `.postN` release.
- If `finalize` fails, rerun it or publish the draft manually with
  `gh release edit <tag> --draft=false --target <sha>`.
- If website deployment or the Release Duty issue fails, rerun that job. These
  jobs do not change the already-published artifacts.
- If draft detection is ambiguous, rerun with an explicit `tag`.

## Legacy fallback and dry run

If the unified workflow is unavailable, publish the GitHub Release to trigger
the retained per-artifact `publish-pypi`, `docker-release`, and
`plugins-release` workflows.

Run **Release (unified)** with `dry_run: true` to exercise resolution, builds,
verification, gating, and draft finalization without uploading PyPI, Docker, or
plugin artifacts.
