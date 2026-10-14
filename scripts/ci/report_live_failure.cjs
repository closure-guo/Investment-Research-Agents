'use strict';
// update-ci-pipeline R5：@live nightly 失败不静默——建/更新 ci-failure issue。
//
// 供 actions/github-script 以 require 方式调用（e2e-playwright.yml live 作业）：
//   const report = require(process.env.GITHUB_WORKSPACE + '/scripts/ci/report_live_failure.cjs')
//   await report({github, context, core})
//
// 去重语义（spec「nightly 失败不静默」+ design「同 run 链接去重」）：
// - 同一失败源（@live nightly）只保留一个 open issue：按标题精确匹配 +
//   ci-failure 标签；已存在则追加评论而非新建（防每日刷屏）；
// - 同一 run 链接不重复评论（run rerun 幂等）；
// - 标签缺失时先补建（GitHub API 对不存在的标签静默丢弃，会悄悄违反
//   「带 ci-failure 标签」的契约，故显式确保）。

const TITLE = '[ci-failure] @live nightly E2E 失败';
const LABEL = 'ci-failure';

async function ensureLabel({ github, context, core }) {
  const { owner, repo } = context.repo;
  try {
    await github.rest.issues.getLabel({ owner, repo, name: LABEL });
  } catch (err) {
    if (err.status !== 404) throw err;
    await github.rest.issues.createLabel({
      owner,
      repo,
      name: LABEL,
      color: 'B60205',
      description: 'CI 失败自动上报（update-ci-pipeline）',
    });
    core.info(`label ${LABEL} 不存在，已创建`);
  }
}

function failureBody({ context, runUrl }) {
  return [
    `失败 run：${runUrl}`,
    '',
    `- 事件：${context.eventName}（${context.ref}）`,
    `- 提交：${context.sha}`,
    `- 尝试：第 ${process.env.GITHUB_RUN_ATTEMPT || 1} 次`,
    '',
    '排查入口：run 日志中 @live 步骤输出；涉及 Langfuse 门禁时先核仓库 Secrets（LANGFUSE_*）。',
  ].join('\n');
}

async function report({ github, context, core }) {
  const { owner, repo } = context.repo;
  const runUrl = `${context.serverUrl}/${owner}/${repo}/actions/runs/${context.runId}`;
  const body = failureBody({ context, runUrl });

  await ensureLabel({ github, context, core });
  const { data: open } = await github.rest.issues.listForRepo({
    owner,
    repo,
    state: 'open',
    labels: LABEL,
    per_page: 100,
  });
  // listForRepo 会同时返回 PR（issue 视图）；只认真 issue，防止同名 PR 被当失败单
  const existing = open.find((issue) => issue.title === TITLE && !issue.pull_request);
  if (!existing) {
    const { data: created } = await github.rest.issues.create({
      owner,
      repo,
      title: TITLE,
      body,
      labels: [LABEL],
    });
    core.info(`已创建 issue #${created.number}: ${TITLE}`);
    return { action: 'created', number: created.number };
  }
  const { data: comments } = await github.rest.issues.listComments({
    owner,
    repo,
    issue_number: existing.number,
    per_page: 100,
  });
  if (comments.some((c) => (c.body || '').includes(runUrl))) {
    core.info(`issue #${existing.number} 已有本 run 记录，跳过（幂等）`);
    return { action: 'noop', number: existing.number };
  }
  await github.rest.issues.createComment({
    owner,
    repo,
    issue_number: existing.number,
    body,
  });
  core.info(`已在 issue #${existing.number} 追加失败记录`);
  return { action: 'commented', number: existing.number };
}

module.exports = report;
