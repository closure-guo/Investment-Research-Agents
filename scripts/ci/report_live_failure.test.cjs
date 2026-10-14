'use strict';
// report_live_failure.cjs 的桩件测试（node 内置 test runner，零依赖）：
//   node --test scripts/ci/report_live_failure.test.cjs
// 覆盖：无 open issue → 建单（含补建标签）；已有单无本 run → 追加评论；
// 已有单且已含本 run → 幂等跳过；同名 PR 不得被当成失败单。

const test = require('node:test');
const assert = require('node:assert');

const report = require('./report_live_failure.cjs');

const CONTEXT = {
  repo: { owner: 'o', repo: 'r' },
  serverUrl: 'https://github.com',
  runId: 999,
  eventName: 'schedule',
  ref: 'refs/heads/main',
  sha: 'abc123',
};
const RUN_URL = 'https://github.com/o/r/actions/runs/999';

function fakeGithub({ openIssues = [], comments = {}, labels = [] } = {}) {
  const calls = { created: [], commented: [], createdLabels: [], listed: 0 };
  return {
    calls,
    rest: {
      issues: {
        async getLabel({ name }) {
          if (!labels.includes(name)) {
            const err = new Error('Not Found');
            err.status = 404;
            throw err;
          }
          return { data: { name } };
        },
        async createLabel(payload) {
          calls.createdLabels.push(payload.name);
          return { data: payload };
        },
        async listForRepo() {
          calls.listed += 1;
          return { data: openIssues };
        },
        async listComments({ issue_number }) {
          return { data: comments[issue_number] || [] };
        },
        async create(payload) {
          calls.created.push(payload);
          return { data: { number: 1 } };
        },
        async createComment(payload) {
          calls.commented.push(payload);
          return { data: {} };
        },
      },
    },
  };
}

const CORE = { info: () => {} };

test('无 open issue：建单并带 ci-failure 标签，标签缺失先补建', async () => {
  const github = fakeGithub({ labels: [] });
  const result = await report({ github, context: CONTEXT, core: CORE });
  assert.deepStrictEqual(result, { action: 'created', number: 1 });
  assert.strictEqual(github.calls.created.length, 1);
  assert.deepStrictEqual(github.calls.created[0].labels, ['ci-failure']);
  assert.ok(github.calls.created[0].body.includes(RUN_URL));
  assert.deepStrictEqual(github.calls.createdLabels, ['ci-failure']);
});

test('已有 open issue 且未含本 run：追加评论而非新建', async () => {
  const github = fakeGithub({
    openIssues: [{ number: 7, title: '[ci-failure] @live nightly E2E 失败' }],
    comments: { 7: [{ body: '旧失败记录 https://github.com/o/r/actions/runs/111' }] },
  });
  const result = await report({ github, context: CONTEXT, core: CORE });
  assert.deepStrictEqual(result, { action: 'commented', number: 7 });
  assert.strictEqual(github.calls.created.length, 0);
  assert.strictEqual(github.calls.commented.length, 1);
  assert.ok(github.calls.commented[0].body.includes(RUN_URL));
});

test('已有 open issue 且评论已含本 run：幂等跳过（rerun 不重复评论）', async () => {
  const github = fakeGithub({
    openIssues: [{ number: 7, title: '[ci-failure] @live nightly E2E 失败' }],
    comments: { 7: [{ body: `失败 run：${RUN_URL}` }] },
  });
  const result = await report({ github, context: CONTEXT, core: CORE });
  assert.deepStrictEqual(result, { action: 'noop', number: 7 });
  assert.strictEqual(github.calls.created.length, 0);
  assert.strictEqual(github.calls.commented.length, 0);
});

test('同名 PR 不被当作失败单（listForRepo 含 PR）', async () => {
  const github = fakeGithub({
    openIssues: [
      { number: 8, title: '[ci-failure] @live nightly E2E 失败', pull_request: { url: 'x' } },
    ],
  });
  const result = await report({ github, context: CONTEXT, core: CORE });
  assert.deepStrictEqual(result, { action: 'created', number: 1 });
});
