<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'

const kbs = ref([])
const activeKb = ref(null)
const docs = ref([])
const messages = ref([])
const input = ref('')
const running = ref(false)
const runId = ref(null)
const runStatus = ref('idle')
const trace = ref(null)
const currentSteps = ref([])
const currentAnswer = ref('')
const currentCitations = ref([])
const currentRefused = ref(false)
const currentConfidence = ref(null)
const fileInput = ref(null)
const evalResult = ref(null)
const error = ref('')
const tenant = ref(localStorage.getItem('nexus.tenant') || 'local')
const audit = ref([])
const toolsOpen = ref(false)
const mcpServers = ref([])
const mcpTools = ref([])
const mcpErrors = ref({})
const skills = ref([])
const toolFilter = ref('')
const settingsOpen = ref(false)
const settings = ref({ mcp_tool_router: true, mcp_cite_external: true })
const mcpMeta = ref({ configured: false, timeout_s: 30 })
const settingsSaving = ref(false)
const backends = ref(null)
const history = ref([])
const drawer = ref(null)
const folded = ref({})
const orgTree = ref([])
const orgDraft = ref({ name: '', parent_id: '' })
const kbDept = ref('')
const navOpen = ref(false)
const panelOpen = ref(false)

const flatOrg = computed(() => {
  const rows = []
  const walk = (nodes, depth) => {
    for (const node of nodes) {
      rows.push({ ...node, depth })
      walk(node.children || [], depth + 1)
    }
  }
  walk(orgTree.value, 0)
  return rows
})

async function refreshOrg() {
  const r = await api('/api/org/tree')
  orgTree.value = r.ok ? await r.json() : []
}

const graph = ref({ nodes: [], edges: [], stats: { nodes: 0, edges: 0, chunks: 0 } })
const graphLoading = ref(false)
const graphNode = ref(null)
const graphPos = ref({})

// A tiny deterministic force layout: no dependency, stable enough for a
// 300px panel, and the same graph always lands in the same shape.
function layoutGraph(payload) {
  const nodes = payload.nodes.map((node, index) => ({ ...node, x: 0, y: 0, i: index }))
  const n = nodes.length
  if (!n) return {}
  const W = 300
  const H = 260
  const radius = Math.min(W, H) / 2 - 26
  nodes.forEach((node, i) => {
    const angle = (i / n) * Math.PI * 2
    node.x = W / 2 + radius * Math.cos(angle)
    node.y = H / 2 + radius * Math.sin(angle)
  })
  const byName = new Map(nodes.map((node) => [node.name, node]))
  const links = payload.edges
    .map((edge) => ({ a: byName.get(edge.source), b: byName.get(edge.target), w: edge.weight || 1 }))
    .filter((link) => link.a && link.b)

  for (let step = 0; step < 220; step += 1) {
    const cooling = 1 - step / 260
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const a = nodes[i]
        const b = nodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let dist2 = dx * dx + dy * dy
        if (dist2 < 1) {
          dx = (i % 2 ? 1 : -1) * 0.6
          dy = 0.4
          dist2 = 1
        }
        const force = (2400 / dist2) * cooling
        const dist = Math.sqrt(dist2)
        a.x += (dx / dist) * force
        a.y += (dy / dist) * force
        b.x -= (dx / dist) * force
        b.y -= (dy / dist) * force
      }
    }
    for (const link of links) {
      const dx = link.b.x - link.a.x
      const dy = link.b.y - link.a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const target = 78
      const pull = ((dist - target) / dist) * 0.06 * cooling
      link.a.x += dx * pull
      link.a.y += dy * pull
      link.b.x -= dx * pull
      link.b.y -= dy * pull
    }
    for (const node of nodes) {
      node.x += (W / 2 - node.x) * 0.012 * cooling
      node.y += (H / 2 - node.y) * 0.012 * cooling
      node.x = Math.max(18, Math.min(W - 18, node.x))
      node.y = Math.max(16, Math.min(H - 16, node.y))
    }
  }
  const out = {}
  for (const node of nodes) out[node.name] = { x: Math.round(node.x), y: Math.round(node.y) }
  return out
}

async function loadGraph() {
  if (!activeKb.value) return
  graphLoading.value = true
  const r = await api(`/api/kb/${activeKb.value.id}/graph?limit=60`)
  if (r.ok) {
    const payload = await r.json()
    graph.value = payload
    graphPos.value = layoutGraph(payload)
  }
  graphLoading.value = false
}

function pickNode(node) {
  graphNode.value = graphNode.value?.name === node.name ? null : node
}

async function openChunk(chunkId) {
  if (!activeKb.value) return
  drawer.value = { chunk_id: chunkId, doc_name: '原文块', heading: '', snippet: '', full: '', loading: true }
  const r = await api(`/api/kb/${activeKb.value.id}/chunks/${chunkId}`)
  if (!drawer.value || drawer.value.chunk_id !== chunkId) return
  if (r.ok) {
    const body = await r.json()
    drawer.value = { ...drawer.value, doc_name: body.doc_name || '原文块', heading: body.heading, full: body.content, loading: false }
  } else {
    drawer.value.loading = false
  }
}

async function addDept() {
  const name = orgDraft.value.name.trim()
  if (!name) return
  const r = await api('/api/departments', {
    method: 'POST',
    body: JSON.stringify({ name, parent_id: orgDraft.value.parent_id || null }),
  })
  if (!r.ok) {
    error.value = (await r.json()).detail || `HTTP ${r.status}`
    return
  }
  orgDraft.value = { name: '', parent_id: '' }
  await refreshOrg()
}

function toggleFold(key) {
  folded.value = { ...folded.value, [key]: !folded.value[key] }
}

async function openCite(citation) {
  drawer.value = { ...citation, full: citation.snippet || '', loading: true }
  if (isExternal(citation) || !activeKb.value) {
    drawer.value.loading = false
    return
  }
  const r = await api(`/api/kb/${activeKb.value.id}/chunks/${citation.chunk_id}`)
  if (!drawer.value || drawer.value.chunk_id !== citation.chunk_id) return
  if (r.ok) {
    const body = await r.json()
    drawer.value = { ...drawer.value, full: body.content || drawer.value.snippet, loading: false }
  } else {
    drawer.value = { ...drawer.value, loading: false }
  }
}

async function refreshBackends() {
  const r = await api('/api/system/backends')
  backends.value = r.ok ? await r.json() : null
}

async function refreshHistory() {
  const r = await api('/api/chat/history?limit=8')
  history.value = r.ok ? (await r.json()).items : []
}

function setTenant(value) {
  tenant.value = value.trim() || 'local'
  localStorage.setItem('nexus.tenant', tenant.value)
}

const api = (path, opts = {}) => fetch(path, {
  headers: {
    'Content-Type': 'application/json',
    'X-Tenant-Id': tenant.value,
    ...(opts.headers || {}),
  },
  ...opts,
})

async function refreshKbs() {
  const r = await api('/api/kb')
  kbs.value = await r.json()
  if (activeKb.value && !kbs.value.some((kb) => kb.id === activeKb.value.id)) {
    activeKb.value = null
    docs.value = []
  }
  if (kbs.value.length && !activeKb.value) {
    activeKb.value = kbs.value[0]
    await refreshDocs()
    await loadGraph()
  }
}

async function refreshAudit() {
  const r = await api('/api/audit?limit=20')
  audit.value = r.ok ? await r.json() : []
}

async function refreshTools(force = false) {
  const [servers, tools, cards] = await Promise.all([
    api('/api/mcp/servers'),
    api(force ? '/api/mcp/refresh' : '/api/mcp/tools', { method: force ? 'POST' : 'GET' }),
    api('/api/skills'),
  ])
  mcpServers.value = servers.ok ? (await servers.json()).items : []
  const payload = tools.ok ? await tools.json() : { items: [], errors: {} }
  mcpTools.value = payload.items || []
  mcpErrors.value = payload.errors || {}
  skills.value = cards.ok ? (await cards.json()).items : []
}

async function refreshSettings() {
  const r = await api('/api/settings')
  if (!r.ok) return
  const body = await r.json()
  settings.value = body.items
  mcpMeta.value = body.mcp
}

async function toggleSetting(key) {
  const next = !settings.value[key]
  settingsSaving.value = true
  try {
    const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ [key]: next }) })
    if (r.ok) settings.value = (await r.json()).items
    else error.value = `保存失败：${r.status}`
  } finally {
    settingsSaving.value = false
  }
}

function toggleSettings() {
  settingsOpen.value = !settingsOpen.value
  if (settingsOpen.value) refreshSettings()
}

function toggleTools() {
  toolsOpen.value = !toolsOpen.value
  if (toolsOpen.value) refreshTools()
}

const groupedTools = computed(() => {
  const needle = toolFilter.value.trim().toLowerCase()
  const groups = new Map()
  for (const tool of mcpTools.value) {
    const hay = `${tool.server}.${tool.name} ${tool.description}`.toLowerCase()
    if (needle && !hay.includes(needle)) continue
    if (!groups.has(tool.server)) groups.set(tool.server, [])
    groups.get(tool.server).push(tool)
  }
  return [...groups.entries()].map(([server, items]) => ({ server, items }))
})

async function refreshDocs() {
  if (!activeKb.value) return
  const r = await api(`/api/kb/${activeKb.value.id}/documents`)
  docs.value = await r.json()
}

async function createKb() {
  const name = prompt('知识库名称：', '新知识库')
  if (!name) return
  await api('/api/kb', {
    method: 'POST',
    body: JSON.stringify({ name, department_id: kbDept.value || null }),
  })
  await refreshKbs()
}

async function selectKb(kb) {
  activeKb.value = kb
  navOpen.value = false
  graphNode.value = null
  graph.value = { nodes: [], edges: [], stats: { nodes: 0, edges: 0, chunks: 0 } }
  await refreshDocs()
  await loadGraph()
}

async function uploadFile() {
  const f = fileInput.value.files[0]
  if (!f || !activeKb.value) return
  const fd = new FormData()
  fd.append('file', f)
  const r = await fetch(`/api/kb/${activeKb.value.id}/documents`, {
    method: 'POST',
    body: fd,
    headers: { 'X-Tenant-Id': tenant.value },
  })
  const res = await r.json()
  if (!r.ok) error.value = res.detail ? JSON.stringify(res.detail) : `HTTP ${r.status}`
  fileInput.value.value = ''
  await refreshDocs()
}

async function deleteDoc(docId) {
  if (!activeKb.value) return
  await api(`/api/kb/${activeKb.value.id}/documents/${docId}`, { method: 'DELETE' })
  await refreshDocs()
}

async function downloadDoc(doc) {
  if (!activeKb.value) return
  const r = await api(`/api/kb/${activeKb.value.id}/documents/${doc.id}/content`)
  if (!r.ok) {
    error.value = '原件未留存：这份文档是在留档功能上线前导入的'
    return
  }
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = doc.filename
  a.click()
  URL.revokeObjectURL(url)
}

function stripMd(text) {
  return text
    .replace(/\*\*/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function fmtSize(n) {
  if (!n && n !== 0) return ''
  if (n < 1024) return `${n} B`
  return `${(n / 1024).toFixed(1)} KB`
}

const STEP_COPY = {
  plan: '拆解问题',
  dispatch: '分发子任务',
  worker: '检索并作答',
  synthesize: '汇总答案',
  finalize: '落库',
}

function stepLabel(key) {
  if (key.startsWith('tool:')) return `调用 ${key.slice(5)}`
  return STEP_COPY[key] || key || '步骤'
}

function stepMark(status) {
  if (status === 'done') return '✓'
  if (status === 'failed' || status === 'warn') return '!'
  return '·'
}

function isExternal(citation) {
  return (citation.doc_id || '').startsWith('mcp:') || citation.heading === '外部工具'
}

const examples = [
  'RRF 是怎么融合三路结果的？',
  '引用校验为什么不能只写进 prompt？',
  '文档里说幻觉率怎么降下来的？',
]

function askExample(question) {
  if (running.value || !activeKb.value) return
  input.value = question
  send()
}

function newHolder() {
  return { role: 'assistant', text: '', raw: '', steps: [], citations: [], refused: false, t0: Date.now(), ms: null }
}

// Maps the engine's SSE contract onto the view model. The backend emits
// run.created / step.started / tool.call / tool.result / guard.violation /
// token / run.completed / run.finished — keeping this in one place means the
// live stream and the replay-after-reconnect path cannot drift apart.
function applyEvent(holder, type, obj) {
  if (type === 'run.created' || type === 'run.started') {
    if (obj.run_id) runId.value = obj.run_id
    runStatus.value = 'running'
  } else if (type === 'step.started') {
    // The pipeline is linear: a new node starting means the previous one moved on.
    for (const s of holder.steps) if (s.status === 'running' && !s.key.startsWith('tool:')) s.status = 'done'
    holder.steps.push({ key: obj.node || '', status: 'running', detail: obj.note || '' })
    currentSteps.value = holder.steps.slice()
  } else if (type === 'step.finished' || type === 'step.completed') {
    const step = holder.steps.find((s) => s.key === obj.node && s.status === 'running')
    if (step) step.status = 'done'
    currentSteps.value = holder.steps.slice()
  } else if (type === 'tool.call') {
    holder.steps.push({
      key: `tool:${obj.tool || ''}`,
      status: 'running',
      detail: obj.args && obj.args.query ? obj.args.query : '',
    })
    currentSteps.value = holder.steps.slice()
  } else if (type === 'tool.result') {
    const step = holder.steps
      .slice()
      .reverse()
      .find((s) => s.key === `tool:${obj.tool || ''}` && s.status === 'running')
    if (step) {
      step.status = obj.ok ? 'done' : 'failed'
      step.detail += ` · ${Number(obj.latency_ms || 0).toFixed(1)} ms`
    }
    currentSteps.value = holder.steps.slice()
  } else if (type === 'guard.violation') {
    holder.steps.push({ key: '引用守卫', status: 'warn', detail: '引用缺失或越界，重写一次' })
    currentSteps.value = holder.steps.slice()
  } else if (type === 'token') {
    holder.raw += obj.text || ''
    holder.text = stripMd(holder.raw)
    currentAnswer.value = holder.text
  } else if (type === 'run.completed') {
    for (const s of holder.steps) if (s.status === 'running') s.status = 'done'
    currentSteps.value = holder.steps.slice()
    if (!holder.raw && obj.answer) holder.raw = obj.answer
    holder.text = stripMd(holder.raw)
    holder.citations = obj.citations || []
    holder.refused = !!obj.refused
    holder.confidence = obj.confidence ?? null
    currentAnswer.value = holder.text
    currentCitations.value = holder.citations
    currentRefused.value = holder.refused
    currentConfidence.value = obj.confidence ?? null
    runStatus.value = 'completed'
  } else if (type === 'run.failed') {
    holder.text = holder.text || `处理失败：${obj.error || ''}`
    runStatus.value = 'failed'
  } else if (type === 'run.finished') {
    return true
  }
  return false
}

async function pump(res, holder) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let finished = false
  while (!finished) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let t = ''
      let d = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) t = line.slice(6).trim()
        else if (line.startsWith('data:')) d += line.slice(5).trim()
      }
      if (!t) continue
      let obj = {}
      try { obj = JSON.parse(d) } catch (e) { /* keepalive or partial frame */ }
      if (applyEvent(holder, t, obj)) finished = true
    }
  }
  if (runStatus.value !== 'failed') runStatus.value = 'completed'
  if (holder.ms === null) holder.ms = Date.now() - holder.t0
  refreshHistory()
}

async function send() {
  const q = input.value.trim()
  if (!q || running.value || !activeKb.value) return
  input.value = ''
  error.value = ''
  messages.value.push({ role: 'user', text: q })
  const holder = newHolder()
  messages.value.push(holder)
  running.value = true
  currentSteps.value = []
  currentAnswer.value = ''
  currentCitations.value = []
  currentRefused.value = false
  currentConfidence.value = null
  trace.value = null

  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenant.value },
    body: JSON.stringify({ kb_id: activeKb.value.id, question: q }),
  })
  if (!res.ok) {
    error.value = `HTTP ${res.status}`
    holder.text = '请求失败'
    running.value = false
    runStatus.value = 'failed'
    return
  }

  try {
    await pump(res, holder)
  } catch (e) {
    error.value = String(e)
    runStatus.value = 'failed'
  }
  running.value = false
  await nextTick()
  const el = document.querySelector('.messages')
  if (el) el.scrollTop = el.scrollHeight
}

async function resumeRun() {
  if (!runId.value) return
  running.value = true
  const holder = newHolder()
  messages.value.push(holder)
  try {
    const res = await fetch(`/api/runs/${runId.value}/stream`, {
      headers: { 'Last-Event-ID': '0', 'X-Tenant-Id': tenant.value },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await pump(res, holder)
  } catch (e) {
    error.value = String(e)
    runStatus.value = 'failed'
  }
  running.value = false
}

async function runEval() {
  evalResult.value = null
  const r = await api('/api/eval/runs', { method: 'POST', body: JSON.stringify({ kb_id: activeKb.value.id, dataset: 'sample' }) })
  const body = await r.json()
  if (!r.ok) {
    error.value = body.detail ? JSON.stringify(body.detail) : `HTTP ${r.status}`
    return
  }
  const m = body.metrics || {}
  evalResult.value = {
    total: m.cases ?? body.results?.length ?? 0,
    recall_at_5: m['recall@5'] ?? '-',
    mrr_at_5: m['mrr@5'] ?? '-',
    faithfulness: m.faithfulness ?? '-',
    zero_hit: m.zero_hit_cases ?? 0,
  }
}

onMounted(() => { refreshKbs(); refreshBackends(); refreshHistory(); refreshOrg() })
</script>

<template>
  <div class="topbar">
    <button class="btn ghost nav-toggle" @click="navOpen = !navOpen" title="知识库与文档" aria-label="知识库与文档">☰</button>
    <div class="brand">
      <span class="mark">N</span>
      <div>
        <h1>NexusAgent</h1>
        <span class="sub">企业知识智能体 · 私有部署 · 答案可溯源</span>
      </div>
    </div>
    <span style="flex:1"></span>
    <span class="tenant-chip" title="所有请求携带 X-Tenant-Id，用于隔离知识库与审计">租户 {{ tenant }}</span>
    <span v-if="runStatus==='running'" class="status-badge running"><i class="pulse"></i>运行中</span>
    <span v-else-if="runStatus==='completed'" class="status-badge completed">已完成</span>
    <span v-else-if="runStatus==='failed'" class="status-badge failed">失败</span>
    <span v-else class="status-badge idle">就绪</span>
    <button class="btn ghost nav-toggle panel-toggle" @click="panelOpen = !panelOpen" title="运行面板" aria-label="运行面板">▤</button>
  </div>

  <div class="main" :class="{ 'nav-open': navOpen, 'panel-open': panelOpen }">
    <div class="scrim" @click="navOpen = false; panelOpen = false"></div>
    <div class="sidebar">
      <div class="side-section">
        <h3>工作空间</h3>
        <input
          class="tenant-input"
          :value="tenant"
          @change="setTenant($event.target.value); refreshKbs()"
          placeholder="X-Tenant-Id"
          title="所有请求都会带上 X-Tenant-Id，用于隔离知识库与审计"
        />
      </div>

      <div class="side-section">
        <h3>知识库 <span class="count">{{ kbs.length }}</span></h3>
        <select v-model="kbDept" class="tenant-input" title="新建的知识库归属哪个部门；留空表示全租户可见">
          <option value="">全租户可见</option>
          <option v-for="row in flatOrg" :key="row.id" :value="row.id">
            {{ '　'.repeat(row.depth) }}{{ row.name }}
          </option>
        </select>
        <button class="btn primary block" @click="createKb">+ 新建知识库</button>
        <div
          v-for="kb in kbs"
          :key="kb.id"
          class="kb-item"
          :class="{active: activeKb && activeKb.id===kb.id}"
          @click="selectKb(kb)"
        >
          <div class="name">{{ kb.name }}</div>
          <div class="meta">{{ kb.document_count }} 文档 · {{ kb.chunk_count }} 块</div>
        </div>
        <p v-if="!kbs.length" class="hint">还没有知识库</p>
      </div>

      <div class="side-section">
        <h3>文档 <span class="count" v-if="activeKb">{{ docs.length }}</span></h3>
        <div v-if="activeKb">
          <label class="upload">
            <input type="file" ref="fileInput" @change="uploadFile" />
            <span class="btn block">选择文件上传</span>
          </label>
          <div v-for="d in docs" :key="d.id" class="doc-item">
            <div class="fname" :title="d.filename">{{ d.filename }}</div>
            <div class="fmeta">{{ d.chunk_count }} 块 · {{ fmtSize(d.size) }}</div>
            <div class="doc-actions">
              <button class="btn ghost sm" @click="downloadDoc(d)">原件</button>
              <button class="btn ghost sm danger" @click="deleteDoc(d.id)">删除</button>
            </div>
          </div>
          <p v-if="!docs.length" class="hint">上传 md / txt / pdf / docx 后会自动切块索引</p>
        </div>
        <p v-else class="hint">先创建或选择知识库</p>
      </div>

      <div class="side-section actions">
        <h3>面板</h3>
        <button class="btn block" :class="{on: toolsOpen}" @click="toggleTools">工具与技能</button>
        <button class="btn block" :class="{on: settingsOpen}" @click="toggleSettings">运行时设置</button>
        <button class="btn block" @click="refreshAudit">审计日志</button>
      </div>
    </div>

    <div class="chat">
      <div class="messages" ref="scrollBox">
        <div v-if="!messages.length" class="empty">
          <div class="empty-mark">N</div>
          <div class="empty-title">问你的知识库</div>
          <p class="empty-sub">检索、引用、拒答都由代码把关，不靠模型自觉。</p>
          <div class="examples">
            <button
              v-for="q in examples"
              :key="q"
              class="example"
              :disabled="!activeKb"
              @click="askExample(q)"
            >
              <span class="example-q">{{ q }}</span>
              <span class="example-go">试试 →</span>
            </button>
          </div>
        </div>
        <div v-for="(m,i) in messages" :key="i" class="msg" :class="[m.role, m.refused?'refused':'']">
          <div class="role">
            <span class="avatar" :class="m.role">{{ m.role==='user'?'你':'N' }}</span>
            <span v-if="m.role!=='user'">NexusAgent</span>
            <span v-if="m.refused" class="tag warn">已拒答</span>
            <span v-if="m.role==='assistant' && m.ms" class="tag quiet">{{ (m.ms/1000).toFixed(1) }}s</span>
          </div>
          <div class="bubble">{{ m.text || (running && i===messages.length-1 ? '…' : '') }}</div>
          <div class="steps" v-if="m.steps && m.steps.length">
            <div
              v-for="(s,j) in m.steps"
              :key="j"
              class="step"
              :class="[s.status]"
            >
              <span class="mark">{{ stepMark(s.status) }}</span>
              <span class="step-name">{{ stepLabel(s.key) }}</span>
              <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
            </div>
          </div>
          <div class="evidence" v-if="m.role==='assistant' && (m.citations?.length || m.confidence!=null)">
            <span v-if="m.confidence!=null" class="tag score">
              置信度 {{ m.confidence.toFixed(2) }}
            </span>
            <button
              v-for="c in m.citations"
              :key="c.chunk_id"
              type="button"
              class="cite"
              :class="{ ext: isExternal(c) }"
              :title="c.snippet"
              @click="openCite(c)"
            >
              <b>{{ c.index }}</b>
              <span class="cite-name">{{ c.doc_name }}</span>
              <span v-if="c.heading" class="cite-head">· {{ c.heading }}</span>
              <span v-if="isExternal(c)" class="cite-label">外部</span>
            </button>
          </div>
        </div>
      </div>
      <div class="inputbar">
        <div class="composer">
          <input
            v-model="input"
            @keyup.enter="send"
            placeholder="输入问题，Enter 发送"
            :disabled="running"
          />
          <span class="composer-hint">{{ running ? '生成中…' : '答案会标注来源，无法溯源时直接拒答' }}</span>
        </div>
        <button class="btn primary" @click="send" :disabled="running || !activeKb">发送</button>
        <button class="btn" @click="resumeRun" :disabled="!runId || running" title="从 DB 回放断线前事件">续传</button>
        <button class="btn ghost" @click="runEval" :disabled="!activeKb || running" title="跑 27 题评估集">评估</button>
      </div>
      <p v-if="error" class="error-line">{{ error }}</p>
    </div>

    <div class="panel">
      <h3>运行面板</h3>

      <div class="trace-block">
        <div class="head">
          <button class="fold" @click="toggleFold('org')">{{ folded.org ? '▸' : '▾' }} 组织架构 · {{ flatOrg.length }} 个部门</button>
          <button class="btn sm" @click="refreshOrg">刷新</button>
        </div>
        <div v-show="!folded.org">
          <div v-for="row in flatOrg" :key="row.id" class="org-row">
            <span class="org-name" :style="{ paddingLeft: `${row.depth * 12}px` }">{{ row.name }}</span>
            <span class="org-role">{{ row.path.split('/').length - 2 }} 级</span>
          </div>
          <div v-if="!flatOrg.length" class="row">还没有部门；知识库默认全租户可见</div>
          <div class="org-form">
            <input v-model="orgDraft.name" placeholder="部门名称" />
            <select v-model="orgDraft.parent_id">
              <option value="">作为根部门</option>
              <option v-for="row in flatOrg" :key="row.id" :value="row.id">
                {{ '　'.repeat(row.depth) }}{{ row.name }} 下
              </option>
            </select>
            <button class="btn sm" @click="addDept">新增</button>
          </div>
        </div>
      </div>

      <div class="trace-block" v-if="activeKb">
        <div class="head">
          <button class="fold" @click="toggleFold('kg')">{{ folded.kg ? '▸' : '▾' }} 知识图谱 · {{ graph.stats.nodes }} 实体 / {{ graph.stats.edges }} 关系</button>
          <button class="btn sm" @click="loadGraph">{{ graphLoading ? '读取中' : '加载' }}</button>
        </div>
        <div v-show="!folded.kg">
          <svg v-if="graph.nodes.length" class="kg" viewBox="0 0 300 260">
            <line
              v-for="(edge, i) in graph.edges"
              :key="'e' + i"
              :x1="graphPos[edge.source]?.x"
              :y1="graphPos[edge.source]?.y"
              :x2="graphPos[edge.target]?.x"
              :y2="graphPos[edge.target]?.y"
              class="kg-edge"
            />
            <g v-for="node in graph.nodes" :key="node.name" class="kg-node" @click="pickNode(node)">
              <circle
                :cx="graphPos[node.name]?.x"
                :cy="graphPos[node.name]?.y"
                :r="4 + Math.min(9, node.degree * 1.6)"
                :class="'kg-' + node.type"
                :stroke="graphNode?.name === node.name ? 'var(--accent)' : 'transparent'"
              />
              <text
                v-if="node.degree >= 2 || graphNode?.name === node.name"
                :x="graphPos[node.name]?.x"
                :y="graphPos[node.name]?.y - 10"
                class="kg-label"
              >{{ node.name }}</text>
            </g>
          </svg>
          <div v-if="!graph.nodes.length" class="row">还没有图谱数据；上传文档后点「加载」</div>
          <div v-if="graphNode" class="kg-detail">
            <div class="kg-title">{{ graphNode.name }} <span class="dim">{{ graphNode.type }} · {{ graphNode.degree }} 条关系</span></div>
            <div class="row">出现在 {{ graphNode.mentions.length }} 个文本块：</div>
            <button v-for="chunkId in graphNode.mentions" :key="chunkId" class="chip" @click="openChunk(chunkId)">
              {{ chunkId.slice(0, 8) }}
            </button>
          </div>
        </div>
      </div>

      <div class="trace-block">
        <div class="head">
          <button class="fold" @click="toggleFold('engine')">{{ folded.engine ? '▸' : '▾' }} 引擎状态</button>
          <button class="btn sm" @click="refreshBackends">刷新</button>
        </div>
        <div v-show="!folded.engine">
        <template v-if="backends">
          <div class="metric"><span>向量库</span><span :class="backends.vector.ok ? 'ok' : 'bad'">{{ backends.vector.backend }}</span></div>
          <div class="metric"><span>知识图谱</span><span :class="backends.graph.ok ? 'ok' : 'bad'">{{ backends.graph.backend }}</span></div>
          <div class="metric"><span>原件存储</span><span :class="backends.objects?.ok ? 'ok' : 'bad'">{{ backends.objects?.backend || '—' }}</span></div>
          <div class="metric"><span>OCR</span><span :class="backends.ocr.backend === 'none' ? 'dim' : (backends.ocr.ok ? 'ok' : 'bad')">{{ backends.ocr.backend }}</span></div>
          <div class="metric"><span>模型 / 嵌入</span><span>{{ backends.llm }} · {{ backends.embedding.provider }}({{ backends.embedding.dim }})</span></div>
        </template>
        <div class="row" v-else>读取中…</div>
        </div>
      </div>

      <div class="trace-block" v-if="activeKb">
        <div class="head">
          <button class="fold" @click="toggleFold('kb')">{{ folded.kb ? '▸' : '▾' }} 当前知识库</button>
        </div>
        <div v-show="!folded.kb">
          <div class="metric"><span>文档</span><span>{{ activeKb.document_count }}</span></div>
          <div class="metric"><span>文本块</span><span>{{ activeKb.chunk_count }}</span></div>
          <div class="row" style="margin-top:6px;color:var(--muted)">
            三路召回 → RRF → 重排 → 引用校验
          </div>
        </div>
      </div>

      <div class="trace-block" v-if="evalResult">
        <div class="head">
          <button class="fold" @click="toggleFold('eval')">{{ folded.eval ? '▸' : '▾' }} 最近一次评估 · {{ evalResult.total }} 题</button>
        </div>
        <div v-show="!folded.eval">
          <div class="metric"><span>Recall@5</span><span class="ok">{{ evalResult.recall_at_5 }}</span></div>
          <div class="metric"><span>MRR@5</span><span>{{ evalResult.mrr_at_5 }}</span></div>
          <div class="metric"><span>Faithfulness</span><span>{{ evalResult.faithfulness }}</span></div>
          <div class="metric"><span>零命中</span><span>{{ evalResult.zero_hit }}</span></div>
        </div>
      </div>

      <div class="trace-block" v-if="history.length">
        <div class="head">
          <button class="fold" @click="toggleFold('history')">{{ folded.history ? '▸' : '▾' }} 最近对话 · {{ history.length }} 条</button>
        </div>
        <div v-show="!folded.history">
          <div class="row history-row" v-for="h in history" :key="h.run_id" :title="h.answer">
            <span class="q">{{ h.question }}</span>
            <span class="tag" :class="h.refused ? 'warn' : 'score'">{{ h.refused ? '拒答' : (h.confidence || 0).toFixed(2) }}</span>
          </div>
        </div>
      </div>

      <div class="trace-block" v-if="audit.length">
        <div class="head">
          <button class="fold" @click="toggleFold('audit')">{{ folded.audit ? '▸' : '▾' }} 审计 · 最近 {{ audit.length }} 条</button>
        </div>
        <div v-show="!folded.audit">
          <div class="row" v-for="a in audit.slice(0, 8)" :key="a.id">
            <span style="color:var(--text-dim)">{{ a.action }}</span>
            <span v-if="a.resource_id"> · {{ a.resource_id.slice(0, 18) }}</span>
          </div>
        </div>
      </div>

      <template v-if="settingsOpen">
        <div class="trace-block">
          <div class="head">运行时开关</div>
          <label class="row toggle-row">
            <input type="checkbox" :checked="settings.mcp_tool_router" :disabled="settingsSaving"
                   @change="toggleSetting('mcp_tool_router')" />
            <span>检索不足时调用只读 MCP 工具</span>
          </label>
          <label class="row toggle-row">
            <input type="checkbox" :checked="settings.mcp_cite_external" :disabled="settingsSaving"
                   @change="toggleSetting('mcp_cite_external')" />
            <span>外部工具结果进入引用来源</span>
          </label>
          <div class="row" style="color:var(--muted)">
            MCP 配置：{{ mcpMeta.configured ? '已加载' : '未配置' }} · 超时 {{ mcpMeta.timeout_s }}s
          </div>
        </div>
      </template>
      <template v-if="toolsOpen">
        <div class="trace-block">
          <div class="head">
            工具目录 · {{ mcpTools.length }} 个
            <button class="btn sm" @click="refreshTools(true)">重新发现</button>
          </div>
          <div class="row" v-for="s in mcpServers" :key="s.name">
            {{ s.name }} · {{ s.type }} · {{ s.connected ? '已连接' : '未连接' }}
            <span v-if="mcpErrors[s.name]" style="color:var(--err)"> — {{ mcpErrors[s.name].slice(0, 60) }}</span>
          </div>
          <input v-model="toolFilter" class="tenant-input" placeholder="按名称过滤" style="margin:6px 0" />
          <template v-for="g in groupedTools" :key="g.server">
            <div class="row" style="margin-top:6px;color:var(--muted)">{{ g.server }} · {{ g.items.length }}</div>
            <div class="row tool-row" v-for="t in g.items" :key="t.server + t.name" :title="t.description">
              <span class="tool-name">{{ t.name }}</span>
              <span class="badge" :class="t.side_effect">{{ t.side_effect === 'read' ? '读' : '写' }}</span>
            </div>
          </template>
          <div class="row" v-if="!groupedTools.length" style="color:var(--muted)">没有匹配的工具</div>
        </div>
        <div class="trace-block">
          <div class="head">Skill 卡片 · {{ skills.length }}</div>
          <div class="row" v-for="k in skills" :key="k.name" :title="k.description">
            {{ k.name }}
          </div>
          <div class="row" v-if="!skills.length" style="color:var(--muted)">未配置 NEXUS_SKILLS_DIR</div>
        </div>
      </template>
      <template v-if="audit.length">
        <div class="trace-block">
          <div class="head">审计日志 · {{ tenant }}</div>
          <div class="row" v-for="a in audit" :key="a.id">
            {{ a.action }} · {{ a.resource_type }} {{ a.resource_id.slice(0, 8) }} · {{ a.created_at.slice(11, 19) }}
          </div>
        </div>
      </template>
      <template v-if="evalResult">
        <div class="metric"><span>样本数</span><span>{{ evalResult.total }}</span></div>
        <div class="metric"><span>Recall@5</span><span>{{ evalResult.recall_at_5 }}</span></div>
        <div class="metric"><span>MRR@5</span><span>{{ evalResult.mrr_at_5 }}</span></div>
        <div class="metric"><span>Faithfulness</span><span>{{ evalResult.faithfulness }}</span></div>
        <div class="metric"><span>Zero-hit</span><span>{{ evalResult.zero_hit }}</span></div>
        <div style="margin-top:8px;font-size:11px;color:var(--muted)">每个样本：gold 命中 top5 记 recall；gold_answer 在答案中记 faithfulness。</div>
      </template>

      <template v-else-if="trace">
        <div v-if="trace.query" class="trace-block">
          <div class="head">检索 Trace</div>
          <div class="row">query: {{ trace.query }}</div>
          <div class="row">channel: dense={{ trace.dense?.length }} bm25={{ trace.bm25?.length }} graph={{ trace.graph?.length }}</div>
          <div v-for="c in trace.retrieved" :key="c.chunk_id" class="trace-block" style="margin-top:4px">
            <div class="head">[{{ c.index }}] {{ c.doc_name }}<span v-if="c.heading"> · {{ c.heading }}</span> · rank {{ c.rank }}</div>
            <div class="row">{{ c.compressed || c.content }}</div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="trace-block" v-if="runId">
          <div class="head">当前运行</div>
          <div class="row">id: {{ runId }}</div>
        </div>
        <div class="trace-block" v-if="currentConfidence !== null">
          <div class="head">置信度</div>
          <div class="row">{{ currentConfidence }}</div>
        </div>
        <div class="trace-block" v-if="currentSteps.length">
          <div class="head">步骤</div>
          <div v-for="(s,i) in currentSteps" :key="i" class="row">
            {{ stepMark(s.status) }} {{ stepLabel(s.key) }}<span v-if="s.detail"> · {{ s.detail }}</span>
          </div>
        </div>
        <div class="trace-block" v-if="currentCitations.length">
          <div class="head">引用</div>
          <div v-for="c in currentCitations" :key="c.chunk_id" class="row">
            [{{ c.index }}] {{ c.doc_name }}<span v-if="c.heading"> · {{ c.heading }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>

  <div v-if="drawer" class="drawer-mask" @click.self="drawer = null">
    <aside class="drawer">
      <header class="drawer-head">
        <div class="drawer-meta">
          <div class="drawer-title"><b>{{ drawer.index }}</b> {{ drawer.doc_name }}</div>
          <div class="drawer-sub">
            <span v-if="drawer.heading">{{ drawer.heading }}</span>
            <span v-if="isExternal(drawer)" class="cite-label">外部工具返回</span>
          </div>
        </div>
        <button class="btn sm" @click="drawer = null">关闭</button>
      </header>
      <pre class="drawer-body">{{ drawer.loading ? "" : drawer.full }}</pre>
      <div v-if="drawer.loading" class="drawer-loading">读取原文…</div>
      <footer class="drawer-foot">chunk: {{ drawer.chunk_id }}</footer>
    </aside>
  </div>
</template>
