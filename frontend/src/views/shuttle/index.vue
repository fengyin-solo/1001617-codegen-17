<template>
  <section class="page" data-module="shuttle">
    <header class="page-head">
      <div>
        <h2>摆渡接送管理</h2>
        <p class="page-desc">选定航班波次一次生成多条摆渡任务，按乘客人数分配车辆；车辆不足与时段冲突的任务单独列出，可只提交一部分。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openDispatch">按航班波次派车</button>
        <button class="btn" type="button" @click="exportRows">导出摆渡任务文件</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>任务编号</span>
        <input v-model="keyword" placeholder="按任务编号检索" />
      </label>
      <label class="filter-item">
        <span>摆渡状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无摆渡接送数据，可先按航班波次派车</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条摆渡接送记录 · 摆渡总人次 {{ summary['摆渡总人次'] ?? 0 }} 人</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 波次派车弹窗 -->
    <div v-if="dispatchOpen" class="modal-mask" @click.self="closeDispatch">
      <div class="modal" role="dialog" aria-modal="true">
        <header class="modal-head">
          <h3>按航班波次派车</h3>
          <button class="link" type="button" @click="closeDispatch">关闭</button>
        </header>

        <div class="modal-body">
          <form class="dispatch-select" @submit.prevent="loadPreview">
            <label class="filter-item">
              <span>航班波次</span>
              <select v-model="selectedWaveId" :disabled="previewLoading">
                <option value="" disabled>请选择波次</option>
                <option v-for="wave in waves" :key="wave.id" :value="wave.id">
                  {{ wave['波次编号'] }} · {{ wave['波次名称'] }}（{{ wave['波次日期'] }} {{ wave['计划时段'] }}，{{ wave.flights.length }} 个航班）
                </option>
              </select>
            </label>
            <button class="btn primary" type="submit" :disabled="!selectedWaveId || previewLoading">
              {{ previewLoading ? '生成中…' : '生成派车方案' }}
            </button>
          </form>

          <p v-if="dispatchError" class="error-text">{{ dispatchError }}</p>

          <template v-if="plan">
            <p class="plan-tip">{{ plan.message }}</p>
            <div class="plan-counts">
              <span class="tag ok">可派 {{ plan.ready_count }} 条</span>
              <span class="tag warn">车辆不足 {{ plan.shortage_count }} 条</span>
              <span class="tag bad">时段冲突 {{ plan.conflict_count }} 条</span>
            </div>

            <h4 class="plan-section">可派任务（勾选后提交）</h4>
            <table class="data-table plan-table">
              <thead>
                <tr>
                  <th class="col-check"><input type="checkbox" :checked="allReadyChecked" :disabled="!readyCandidates.length" @change="toggleAllReady" /></th>
                  <th>预测编号</th><th>关联航班</th><th>乘客人数</th><th>车辆编号</th><th>核定载客</th>
                  <th>出发时刻</th><th>到达时刻</th><th>驾驶人员</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="cand in readyCandidates" :key="cand.index">
                  <td class="col-check"><input type="checkbox" v-model="checkedIndexes" :value="cand.index" /></td>
                  <td>{{ cand['任务编号'] }}</td>
                  <td>{{ cand['关联航班'] }}</td>
                  <td>{{ cand['乘客人数'] }}</td>
                  <td>{{ cand['车辆编号'] }}</td>
                  <td>{{ cand['核定载客'] }}</td>
                  <td>{{ cand['出发时刻'] }}</td>
                  <td>{{ cand['到达时刻'] }}</td>
                  <td>{{ cand['驾驶人员'] || '—' }}</td>
                </tr>
                <tr v-if="!readyCandidates.length">
                  <td colspan="9" class="empty-state">该波次没有可派任务</td>
                </tr>
              </tbody>
            </table>

            <template v-if="shortageCandidates.length">
              <h4 class="plan-section warn-text">车辆不足（不会提交）</h4>
              <ul class="blocked-list">
                <li v-for="cand in shortageCandidates" :key="cand.index">
                  <strong>{{ cand['关联航班'] }}</strong> · {{ cand['乘客人数'] }} 人 · {{ cand['出发时刻'] }}~{{ cand['到达时刻'] }}
                  <span class="block-reason">{{ cand.reason }}</span>
                </li>
              </ul>
            </template>

            <template v-if="conflictCandidates.length">
              <h4 class="plan-section bad-text">时段冲突（不会提交）</h4>
              <ul class="blocked-list">
                <li v-for="cand in conflictCandidates" :key="cand.index">
                  <strong>{{ cand['关联航班'] }}</strong> · {{ cand['乘客人数'] }} 人 · {{ cand['出发时刻'] }}~{{ cand['到达时刻'] }}
                  <span class="block-reason">{{ cand.reason }}</span>
                </li>
              </ul>
            </template>
          </template>
        </div>

        <footer class="modal-foot">
          <span v-if="commitMessage" :class="commitOk ? 'ok-text' : 'error-text'">{{ commitMessage }}</span>
          <span class="foot-spacer" />
          <button class="btn" type="button" @click="closeDispatch">关闭</button>
          <button
            class="btn primary"
            type="button"
            :disabled="!plan || !checkedIndexes.length || commitLoading"
            @click="submitPlan"
          >
            {{ commitLoading ? '提交中…' : `提交所选 ${checkedIndexes.length} 条` }}
          </button>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type Candidate = {
  index: number
  任务编号: string | null
  关联航班: string
  车辆编号: string | null
  核定载客: number | null
  乘客人数: number
  出发时刻: string
  到达时刻: string
  驾驶人员: string | null
  blocked: boolean
  block_type: '车辆不足' | '时段冲突' | null
  reason: string
  conflict_with: string[]
}

type Wave = {
  id: number
  波次编号: string
  波次名称: string
  波次日期: string
  计划时段: string
  flights: { 航班号: string; 乘客人数: number; 出发时刻: string; 到达时刻: string }[]
}

type Plan = {
  ok: boolean
  message: string
  wave: Wave | null
  candidates: Candidate[]
  ready_count: number
  shortage_count: number
  conflict_count: number
  created?: Row[]
  skipped?: { index: number; block_type: string | null; reason: string }[]
}

const ENDPOINT = '/api/shuttle'
const columns = ['任务编号', '关联航班', '车辆编号', '乘客人数', '出发时刻', '到达时刻', '驾驶人员', '摆渡状态']
const actions = ['安排发车', '确认送达', '取消任务']
const statuses = ['待发车', '行驶中', '已送达', '已取消']

const rows = ref<Row[]>([])
const total = ref(0)
const summary = ref<Record<string, number>>({})
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')

const statCards = computed(() => [
  { label: '待发车任务', value: summary.value['待发车任务'] ?? 0 },
  { label: '摆渡总人次', value: summary.value['摆渡总人次'] ?? 0 },
  { label: '取消任务数', value: summary.value['取消任务数'] ?? 0 },
])

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  // 与列表页同过滤口径，后端返回 CSV 文件；用显式 download 链接而不是新标签页。
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  query.set('format', 'csv')
  const link = document.createElement('a')
  link.href = `${ENDPOINT}/export?${query.toString()}`
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => ({ ok: false, message: '摆渡接送动作未生效' }))
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '摆渡接送动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '摆渡接送操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('摆渡任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    summary.value = payload.summary ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '摆渡接送列表读取失败'
  }
}

// ---------- 波次派车 ----------
const dispatchOpen = ref(false)
const waves = ref<Wave[]>([])
const selectedWaveId = ref<number | ''>('')
const plan = ref<Plan | null>(null)
const checkedIndexes = ref<number[]>([])
const previewLoading = ref(false)
const commitLoading = ref(false)
const dispatchError = ref('')
const commitMessage = ref('')
const commitOk = ref(true)

const readyCandidates = computed(() => (plan.value?.candidates ?? []).filter((candidate) => !candidate.blocked))
const shortageCandidates = computed(() =>
  (plan.value?.candidates ?? []).filter((candidate) => candidate.blocked && candidate.block_type === '车辆不足'),
)
const conflictCandidates = computed(() =>
  (plan.value?.candidates ?? []).filter((candidate) => candidate.blocked && candidate.block_type === '时段冲突'),
)
const allReadyChecked = computed(
  () => readyCandidates.value.length > 0 && readyCandidates.value.every((candidate) => checkedIndexes.value.includes(candidate.index)),
)

async function openDispatch() {
  dispatchOpen.value = true
  dispatchError.value = ''
  commitMessage.value = ''
  plan.value = null
  checkedIndexes.value = []
  try {
    const response = await request(`${ENDPOINT}/dispatch/waves`)
    if (!response.ok) {
      throw new Error('航班波次读取失败')
    }
    const payload = await response.json()
    waves.value = payload.items ?? []
  } catch (error) {
    dispatchError.value = error instanceof Error ? error.message : '航班波次读取失败'
  }
}

function closeDispatch() {
  dispatchOpen.value = false
}

function toggleAllReady(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  checkedIndexes.value = checked ? readyCandidates.value.map((candidate) => candidate.index) : []
}

async function loadPreview() {
  if (!selectedWaveId.value) {
    return
  }
  previewLoading.value = true
  dispatchError.value = ''
  commitMessage.value = ''
  plan.value = null
  checkedIndexes.value = []
  try {
    const response = await request(`${ENDPOINT}/dispatch/preview/${selectedWaveId.value}`)
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      throw new Error(payload?.detail || '派车方案生成失败')
    }
    plan.value = payload as Plan
    checkedIndexes.value = readyCandidates.value.map((candidate) => candidate.index)
  } catch (error) {
    dispatchError.value = error instanceof Error ? error.message : '派车方案生成失败'
  } finally {
    previewLoading.value = false
  }
}

async function submitPlan() {
  if (!selectedWaveId.value || !checkedIndexes.value.length) {
    return
  }
  commitLoading.value = true
  commitMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/dispatch/commit`, {
      method: 'POST',
      body: JSON.stringify({ wave_id: selectedWaveId.value, selected: checkedIndexes.value }),
    })
    const payload = (await response.json().catch(() => null)) as Plan | null
    if (!response.ok || !payload) {
      throw new Error((payload as unknown as { detail?: string })?.detail || '派车提交失败')
    }
    plan.value = payload
    checkedIndexes.value = readyCandidates.value.map((candidate) => candidate.index)
    commitOk.value = Boolean(payload.ok)
    commitMessage.value = payload.message
    // 落库后立即刷新列表与人数清点，保证列表/统计/导出三处口径一致。
    await reload()
  } catch (error) {
    commitOk.value = false
    commitMessage.value = error instanceof Error ? error.message : '派车提交失败'
  } finally {
    commitLoading.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
}
.filter-bar select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  background: #fff;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 40px 16px;
  z-index: 50;
  overflow-y: auto;
}
.modal {
  background: #fff;
  border-radius: 10px;
  width: min(960px, 100%);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.25);
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}
.modal-head h3 {
  margin: 0;
  font-size: 16px;
}
.modal-body {
  padding: 14px 18px;
  max-height: 62vh;
  overflow-y: auto;
}
.modal-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
}
.foot-spacer {
  flex: 1;
}
.dispatch-select {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 10px;
}
.dispatch-select select {
  min-width: 420px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  background: #fff;
}
.plan-tip {
  margin: 8px 0;
  font-size: 13px;
  color: var(--muted);
}
.plan-counts {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.tag {
  font-size: 12px;
  border-radius: 999px;
  padding: 2px 10px;
  border: 1px solid var(--border);
}
.tag.ok {
  color: #067647;
  background: #ecfdf3;
  border-color: #abefc6;
}
.tag.warn {
  color: #b54708;
  background: #fffaeb;
  border-color: #fedf89;
}
.tag.bad {
  color: #b42318;
  background: #fef3f2;
  border-color: #fda29b;
}
.plan-section {
  margin: 14px 0 6px;
  font-size: 13px;
}
.col-check {
  width: 34px;
  text-align: center;
}
.plan-table th,
.plan-table td {
  font-size: 12px;
  padding: 6px 8px;
}
.blocked-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.block-reason {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.warn-text {
  color: #b54708;
}
.bad-text {
  color: #b42318;
}
.ok-text {
  color: #067647;
}
</style>
