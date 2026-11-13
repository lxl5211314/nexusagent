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
  }
}

async function refreshAudit() {
  const r = await api('/api/audit?limit=20')
  audit.value = r.ok ? await r.json() : []
}

async function refreshDocs() {
  if (!activeKb.value) return
  const r = await api(`/api/kb/${activeKb.value.id}/documents`)
  docs.value = await r.json()
}

async function createKb() {
  const name = prompt('知识库名称：', '新知识库')
  if (!name) return
  await api('/api/kb', { method: 'POST', body: JSON.stringify({ name }) })
  await refreshKbs()
}

async function selectKb(kb) {
  activeKb.value = kb
  await refreshDocs()
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

function stripMd(text) {
  return text
    .replace(/\[(\d+)\]/g, '')
    .replace(/\*\*/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

async function send() {
  const q = input.value.trim()
  if (!q || running.value || !activeKb.value) return
  input.value = ''
  error.value = ''
  messages.value.push({ role: 'user', text: q })
  const holder = { role: 'assistant', text: '', steps: [], citations: [], refused: false }
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

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let eventType = ''
  let eventData = ''
  let finished = false

  const handleEvent = (type, data) => {
    let obj = {}
    try { obj = JSON.parse(data) } catch (e) {}
    if (type === 'run.started') {
      runId.value = obj.run_id || null
      runStatus.value = 'running'
    } else if (type === 'run.step') {
      const step = { key: obj.key || '', status: obj.status || '', detail: obj.detail || '' }
      holder.steps.push(step)
      currentSteps.value = holder.steps
    } else if (type === 'run.answer') {
      holder.text = stripMd(obj.answer || '')
      holder.citations = obj.citations || []
      holder.refused = !!obj.refused
      currentAnswer.value = holder.text
      currentCitations.value = holder.citations
      currentRefused.value = holder.refused
      currentConfidence.value = obj.confidence ?? null
    } else if (type === 'run.trace') {
      trace.value = obj
    } else if (type === 'run.completed') {
      runStatus.value = 'completed'
    } else if (type === 'run.failed') {
      holder.text = holder.text || '处理失败'
      runStatus.value = 'failed'
    } else if (type === 'run.finished') {
      finished = true
    }
  }

  while (!finished) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let t = '', d = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) t = line.slice(6).trim()
        else if (line.startsWith('data:')) d += line.slice(5).trim()
      }
      if (t) handleEvent(t, d)
    }
  }
  running.value = false
  if (runStatus.value !== 'failed') runStatus.value = 'completed'
  await nextTick()
  const el = document.querySelector('.messages')
  if (el) el.scrollTop = el.scrollHeight
}

async function resumeRun() {
  if (!runId.value) return
  const res = await fetch(`/api/runs/${runId.value}/stream`, {
    headers: { 'Last-Event-ID': '0', 'X-Tenant-Id': tenant.value },
  })
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
      let t = '', d = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) t = line.slice(6).trim()
        else if (line.startsWith('data:')) d += line.slice(5).trim()
      }
      if (t === 'run.finished') finished = true
    }
  }
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

onMounted(() => { refreshKbs() })
</script>

<template>
  <div class="topbar">
    <h1>NexusAgent</h1>
    <span class="sub">企业知识智能体 · MVP · 离线可跑</span>
    <span style="flex:1"></span>
    <span v-if="runStatus==='running'" class="status-badge running">运行中</span>
    <span v-else-if="runStatus==='completed'" class="status-badge completed">完成</span>
    <span v-else-if="runStatus==='failed'" class="status-badge failed">失败</span>
  </div>

  <div class="main">
    <div class="sidebar">
      <h3>租户</h3>
      <input
        class="tenant-input"
        :value="tenant"
        @change="setTenant($event.target.value); refreshKbs()"
        placeholder="X-Tenant-Id"
        title="所有请求都会带上 X-Tenant-Id，用于隔离知识库与审计"
      />
      <h3>知识库</h3>
      <button class="btn" @click="createKb" style="width:100%;margin-bottom:8px">+ 新建知识库</button>
      <div v-for="kb in kbs" :key="kb.id" class="kb-item" :class="{active: activeKb && activeKb.id===kb.id}" @click="selectKb(kb)">
        <div class="name">{{ kb.name }}</div>
        <div class="meta">{{ kb.document_count }} 文档 · {{ kb.chunk_count }} 块</div>
      </div>

      <h3>文档上传</h3>
      <button class="btn" @click="refreshAudit" style="width:100%;margin-bottom:8px">查看审计日志</button>
      <div v-if="activeKb">
        <div class="upload">
          <input type="file" ref="fileInput" @change="uploadFile" />
        </div>
        <div v-for="d in docs" :key="d.id" class="doc-item">
          <div class="fname">{{ d.filename }}</div>
          <div class="fmeta">{{ d.chunk_count }} 块 · {{ d.size_bytes }} B</div>
          <button class="btn" @click="deleteDoc(d.id)" style="margin-top:2px;padding:2px 6px">删除</button>
        </div>
      </div>
      <p v-else class="sub">先创建或选择知识库</p>
    </div>

    <div class="chat">
      <div class="messages">
        <div v-if="!messages.length" class="sub" style="padding:30px;text-align:center">
          提问关于知识库的问题，例如「RRF 融合公式的 k 取多少？」<br/>
          支持断线续传：刷新后点「续传」。
        </div>
        <div v-for="(m,i) in messages" :key="i" class="msg" :class="[m.role, m.refused?'refused':'']">
          <div class="role">{{ m.role==='user'?'你':'NexusAgent' }}{{ m.refused?'（已拒答）':'' }}</div>
          <div class="bubble">{{ m.text || (running && i===messages.length-1 ? '…' : '') }}</div>
          <div class="steps" v-if="m.steps && m.steps.length">
            <div v-for="(s,j) in m.steps" :key="j" class="step" :class="{done: s.status==='done', active: s.status==='running'}">
              <span class="dot"></span>{{ s.key }} — {{ s.detail }}
            </div>
          </div>
          <div class="citations" v-if="m.citations && m.citations.length">
            引用：
            <a v-for="c in m.citations" :key="c.chunk_id" :title="c.content">{{ c.doc_name }} #[{{ c.ordinal }}]</a>
          </div>
        </div>
      </div>
      <div class="inputbar">
        <input v-model="input" @keyup.enter="send" placeholder="输入问题，Enter 发送" :disabled="running" />
        <button @click="send" :disabled="running || !activeKb">发送</button>
        <button @click="resumeRun" :disabled="!runId || running" title="从 DB 回放断线前事件">续传</button>
        <button @click="runEval" :disabled="!activeKb || running" title="跑评估集">评估</button>
      </div>
      <p v-if="error" style="color:var(--err);padding:0 12px;margin:0 0 8px">{{ error }}</p>
    </div>

    <div class="panel">
      <h3>运行面板</h3>
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
            <div class="head">{{ c.doc_name }} #[{{ c.ordinal }}] · rank {{ c.rank }} · {{ (c.score*100).toFixed(0) }}%</div>
            <div class="row">{{ c.compressed || c.content }}</div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="trace-block" v-if="runId"><div class="head">Run</div><div class="row">id: {{ runId }}</div></div>
        <div class="trace-block" v-if="currentConfidence !== null"><div class="head">置信度</div><div class="row">{{ currentConfidence }}</div></div>
        <div class="trace-block" v-if="currentSteps.length">
          <div class="head">步骤</div>
          <div v-for="(s,i) in currentSteps" :key="i" class="row">{{ s.status }} · {{ s.key }} · {{ s.detail }}</div>
        </div>
        <div class="trace-block" v-if="currentCitations.length">
          <div class="head">引用</div>
          <div v-for="c in currentCitations" :key="c.chunk_id" class="row">{{ c.doc_name }} #[{{ c.ordinal }}] · {{ c.content }}</div>
        </div>
      </template>
    </div>
  </div>
</template>
