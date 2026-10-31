/**
 * E2E config env 键集一致性守卫（issue #499/#519）。
 *
 * 背景：playwright.isolated.config.ts（本地隔离端口对 8001/5174）的后端 env 块
 * 与 playwright.config.ts（CI/默认 8000/5173）是两份手工维护的清单。历史上已
 * 两次实证漏项的后果：
 *   - 收割三 env 缺失 → factor-harvest-ui 收割块在隔离跑下永不渲染（#499）
 *   - SCREENING_RULES_CONFIG_PATH 缺失 → settings-screening-rules 用例读写仓库
 *     真实设置路径（#519，主检出跑会污染真实文件）
 *
 * 本脚本解析两份 config 的后端 webServer env 键集，要求完全一致才退出 0。
 * 已知且有意保留的值差异键在 ALLOWED_VALUE_DIFF 中登记（键仍必须在两边同时存在）。
 *
 * 用法：node scripts/check-config-env-parity.mjs（工作目录 tests/e2e/playwright）
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))

/** 键在两边都存在但值有意不同的白名单（值差异本身不做比较） */
const ALLOWED_VALUE_DIFF = new Set([
  // 隔离报告目录按后端端口区分（tmp/e2e-reports-8000 vs tmp/e2e-reports-8001）
  'REPORTS_DIR',
])

function extractBackendEnvKeys(filePath) {
  const src = readFileSync(filePath, 'utf8')
  // 定位后端 webServer（uvicorn 命令）之后的 env: { ... } 块，做括号配对提取
  const backendIdx = src.indexOf('uvicorn finance_agent.api:app')
  if (backendIdx === -1) throw new Error(`${filePath}: 找不到后端 uvicorn webServer`)
  const envIdx = src.indexOf('env: {', backendIdx)
  if (envIdx === -1) throw new Error(`${filePath}: 后端 webServer 缺 env 块`)
  let depth = 0
  let end = -1
  for (let i = envIdx + 'env:'.length; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') {
      depth--
      if (depth === 0) {
        end = i
        break
      }
    }
  }
  if (end === -1) throw new Error(`${filePath}: env 块括号不配对`)
  // 剥掉 // 与 /* */ 注释再提取键——isolated config 的 env 块是带行间注释的多行形态，
  // 不剥注释会把「前一行以注释结尾」的键漏掉（首次运行实证）
  const body = src
    .slice(envIdx, end + 1)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
  const keys = new Set()
  // 仅匹配对象字面量键位（行首/逗号后的 KEY: 形态）
  for (const m of body.matchAll(/[{,]\s*([A-Z_][A-Z0-9_]*)\s*:/g)) keys.add(m[1])
  if (!keys.has('TESTING')) throw new Error(`${filePath}: env 键提取异常（无 TESTING）`)
  return keys
}

const root = join(here, '..', '..', '..', '..')
const defaultKeys = extractBackendEnvKeys(join(root, 'tests', 'e2e', 'playwright', 'playwright.config.ts'))
const isolatedKeys = extractBackendEnvKeys(join(root, 'tests', 'e2e', 'playwright', 'playwright.isolated.config.ts'))

const missingInIsolated = [...defaultKeys].filter((k) => !isolatedKeys.has(k))
const missingInDefault = [...isolatedKeys].filter((k) => !defaultKeys.has(k))

if (missingInIsolated.length || missingInDefault.length) {
  console.error('E2E config env 键集不一致（issue #499/#519 守卫）：')
  if (missingInIsolated.length) {
    console.error('  isolated config 缺失（对齐 playwright.config.ts 补齐）:')
    for (const k of missingInIsolated) console.error(`    - ${k}`)
  }
  if (missingInDefault.length) {
    console.error('  default config 缺失（对齐 playwright.isolated.config.ts 补齐）:')
    for (const k of missingInDefault) console.error(`    - ${k}`)
  }
  console.error('  两份清单是手工维护的，新增后端 env 时必须两处同步；')
  console.error('  值有意不同的键请登记到本脚本 ALLOWED_VALUE_DIFF 并注释理由。')
  process.exit(1)
}

const valueDiffKeys = ALLOWED_VALUE_DIFF.size
console.log(
  `E2E config env 键集一致：${defaultKeys.size} 键（值差异白名单 ${valueDiffKeys} 项：${[...ALLOWED_VALUE_DIFF].join(', ')}）`,
)
