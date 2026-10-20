// E2E dev server supervisor (issue #422).
//
// Playwright `webServer` 直接拉起 `npm run dev` / `uv run uvicorn` 时，进程若在
// 套件中途被外部强杀，只会表现为后续用例 ERR_CONNECTION_REFUSED，且没有任何
// exit line。本包装器：
//   1. 记录子进程退出码/信号与时间（可区分「自身崩溃」与「被强杀」）；
//   2. 意外退出时自动重启（同一端口，最多 MAX_RESTARTS 次），避免单次进程
//      死亡级联整段套件；
//   3. 收到 SIGTERM/SIGINT（Playwright teardown）时不再重启，并杀掉子进程树。
//
// 用法：node dev-supervisor.mjs --port <观察端口> -- <完整启动命令>
//   例：node dev-supervisor.mjs --port 5175 -- npm run dev -- --port 5175
import { spawn } from 'node:child_process'

const argv = process.argv.slice(2)
const sep = argv.indexOf('--')
const opts = sep >= 0 ? argv.slice(0, sep) : argv
const cmdArgs = sep >= 0 ? argv.slice(sep + 1) : []
const portIdx = opts.indexOf('--port')
const port = portIdx >= 0 ? opts[portIdx + 1] : 'unknown'
const command = cmdArgs.join(' ')
const MAX_RESTARTS = 5

if (!command) {
  process.stderr.write('[dev-sup] missing command: node dev-supervisor.mjs --port <p> -- <cmd>\n')
  process.exit(2)
}

let stopping = false
let restarts = 0
let child = null

function log(msg) {
  process.stderr.write(`[dev-sup:${port}] ${msg}\n`)
}

function killTree() {
  if (!child?.pid) return
  if (process.platform === 'win32') {
    try {
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
    } catch {
      /* ignore */
    }
  } else {
    try {
      process.kill(-child.pid, 'SIGKILL')
    } catch {
      /* ignore */
    }
  }
}

function start() {
  log(`spawn "${command}" (restart #${restarts})`)
  // shell: true 让 npm / uv 等入口解析与直接 webServer 一致；不给 args 数组，
  // 避免 Node DEP0190 的拼接告警（整条命令按 shell 语义执行）。
  child = spawn(command, { env: process.env, stdio: ['ignore', 'pipe', 'pipe'], shell: true })
  child.stdout.on('data', (d) => process.stdout.write(d))
  child.stderr.on('data', (d) => process.stderr.write(d))
  child.on('exit', (code, signal) => {
    log(`child exit code=${code} signal=${signal} at ${new Date().toISOString()}`)
    if (stopping) {
      process.exit(code ?? 0)
    }
    if (restarts >= MAX_RESTARTS) {
      log(`too many restarts (${restarts}); giving up`)
      process.exit(1)
    }
    restarts += 1
    setTimeout(start, 500)
  })
}

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    stopping = true
    killTree()
    process.exit(0)
  })
}

start()
