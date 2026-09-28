<template>
  <section class="page" data-module="shuttle">
    <header class="page-head">
      <div>
        <h2>摆渡接送管理</h2>
        <p class="page-desc">按航班波次一次生成多条摆渡任务，按乘客人数分配车辆，自动校验时刻先后与车辆时段占用；已派车任务可导出下载。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openDispatch">按波次派车</button>
        <button class="btn" type="button" @click="openCreate">登记摆渡任务</button>
        <button class="btn" type="button" @click="exportFile">导出摆渡清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>任务编号</span>
        <input v-model="filters.keyword" placeholder="按任务编号检索" />
      </label>
      <label class="filter-item">
        <span>摆渡状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>航班波次</span>
        <input v-model="filters.wave" placeholder="按波次编号检索" />
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
      <span>共 {{ total }} 条摆渡接送记录 · 当前口径乘客合计 {{ headCount }} 人</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 按航班波次派车 -->
    <div v-if="dispatchVisible" class="modal-mask" @click.self="closeDispatch">
      <div class="modal">
        <div class="modal-head">
          <h3>按航班波次派车</h3>
          <button class="link" type="button" @click="closeDispatch">关闭</button>
        </div>

        <div class="modal-body">
          <form class="wave-bar" @submit.prevent="generatePlan">
            <label class="filter-item">
              <span>航班波次</span>
              <select v-model="selectedWaveId" :disabled="loadingWaves">
                <option value="" disabled>请选择航班波次</option>
                <option v-for="wave in waves" :key="wave.id" :value="wave.id">
                  {{ wave.波次编号 }} · {{ wave.波次名称 }}（{{ wave.航班数量 }} 班 / {{ wave.乘客合计 }} 人）
                </option>
              </select>
            </label>
            <button class="btn primary" type="submit" :disabled="!selectedWaveId || generating">
              {{ generating ? '生成中…' : '生成派车方案' }}
            </button>
          </form>

          <div v-if="plan" class="plan-summary">
            <span>波次 {{ plan.wave.波次编号 }}（{{ plan.wave.波次日期 }}）</span>
            <span class="ok-text">可派 {{ plan.available_count }} 条</span>
            <span class="error-text" v-if="plan.rejected_count">车辆不够/时段冲突 {{ plan.rejected_count }} 条</span>
            <span>清点乘客 {{ plan.head_count }} 人</span>
          </div>

          <table v-if="plan" class="data-table plan-table">
            <thead>
              <tr>
                <th class="col-check">
                  <input
                    type="checkbox"
                    :checked="allValidChecked"
                    :disabled="!validCandidates.length"
                    @change="toggleAllValid"
                  />
                </th>
                <th>候选任务</th>
                <th>关联航班</th>
                <th>乘客人数</th>
                <th>出发时刻</th>
                <th>到达时刻</th>
                <th>分配车辆</th>
                <th>校验结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="candidate in plan.candidates" :key="candidate.item_key" :class="{ 'row-invalid': !candidate.valid }">
                <td>
                  <input
                    v-if="candidate.valid"
                    type="checkbox"
                    :value="candidate.item_key"
                    v-model="checkedKeys"
                  />
                  <span v-else class="muted-text">不可派</span>
                </td>
                <td>{{ candidate.item_key }}</td>
                <td>{{ candidate.关联航班 }}</td>
                <td>{{ candidate.乘客人数 }}</td>
                <td>{{ candidate.出发时刻 }}</td>
                <td>{{ candidate.到达时刻 }}</td>
                <td>{{ candidate.车辆编号 ?? '—' }}</td>
                <td>
                  <span v-if="candidate.valid" class="ok-text">可派车</span>
                  <span v-else class="error-text">{{ candidate.reason }}</span>
                </td>
              </tr>
            </tbody>
          </table>

          <div v-if="submitResult" class="submit-result">
            <p :class="submitResult.created_count ? 'ok-text' : 'error-text'">{{ submitResult.message }}</p>
            <ul v-if="submitResult.created_count" class="result-list ok-text">
              <li v-for="entry in submitResult.created" :key="String(entry.任务编号)">
                已生成 {{ entry.任务编号 }}：{{ entry.关联航班 }} / {{ entry.车辆编号 }} / {{ entry.乘客人数 }} 人
              </li>
            </ul>
            <ul v-if="submitResult.blocked_count" class="result-list error-text">
              <li v-for="block in submitResult.blocked" :key="block.item_key">
                未提交（{{ block.item_key }} · {{ block.关联航班 }}）：{{ block.reason }}
              </li>
            </ul>
          </div>

          <p v-if="dispatchError" class="error-text">{{ dispatchError }}</p>
        </div>

        <div class="modal-foot">
          <span class="muted-text">已勾选 {{ checkedKeys.length }} 条，可只提交其中一部分</span>
          <span>
            <button class="btn ghost" type="button" @click="closeDispatch">取消</button>
            <button class="btn primary" type="button" :disabled="!checkedKeys.length || submitting" @click="submitPlan">
              {{ submitting ? '提交中…' : '提交勾选任务' }}
            </button>
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Candidate = {
  item_key: string
  关联航班: string
  乘客人数: number
  出发时刻: string
  到达时刻: string
  车辆编号: string | null
  valid: boolean
  reason: string
}
type WaveOption = {
  id: number
  波次编号: string
  波次名称: string
  波次日期: string
  航班数量: number
  乘客合计: number
}
type Plan = {
  wave: WaveOption
  candidates: Candidate[]
  available_count: number
  rejected_count: number
  head_count: number
}
type SubmitResult = {
  ok: boolean
  message: string
  created: Row[]
  blocked: Array<Record<string, string | number>>
  created_count: number
  blocked_count: number
}

const ENDPOINT = '/api/shuttle'
const columns = ['任务编号', '航班波次', '关联航班', '车辆编号', '乘客人数', '出发时刻', '到达时刻', '驾驶人员', '摆渡状态']
const actions = ['安排发车', '确认送达', '取消任务']
const statuses = ['待发车', '行驶中', '已送达', '已取消']

const rows = ref<Row[]>([])
const total = ref(0)
const headCount = ref(0)
const statusCounts = ref<Record<string, number>>({})
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '', wave: '' })

const stats = computed(() => [
  { label: '当前口径任务', value: total.value },
  { label: '清点乘客（人）', value: headCount.value },
  { label: '待发车任务', value: statusCounts.value['待发车'] ?? 0 },
  { label: '行驶中任务', value: statusCounts.value['行驶中'] ?? 0 },
])

function resetFilters() {
  filters.value = { keyword: '', status: '', wave: '' }
  void reload()
}

function currentQuery(): string {
  const params: Record<string, string> = {}
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params[key] = value
  }
  return new URLSearchParams(params).toString()
}

async function reload() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${currentQuery()}`)
    if (!response.ok) {
      throw new Error('摆渡任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    headCount.value = payload.head_count ?? 0
    statusCounts.value = payload.status_counts ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '摆渡接送列表读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('摆渡接送动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '摆渡接送操作失败'
  }
}

function openCreate() {
  errorMessage.value = '摆渡任务登记入口尚未接入审批流，新增任务请使用「按波次派车」'
}

// ---------- 导出文件：与列表页同一过滤口径，下载带 BOM 的 CSV ----------

async function exportFile() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/export/file?${currentQuery()}`)
    if (!response.ok) {
      throw new Error('摆渡清单导出失败')
    }
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const matched = /filename\*=UTF-8''(.+)$/.exec(disposition)
    const filename = matched ? decodeURIComponent(matched[1]) : '摆渡接送清单.csv'
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '摆渡清单导出失败'
  }
}

// ---------- 按航班波次派车 ----------

const dispatchVisible = ref(false)
const waves = ref<WaveOption[]>([])
const selectedWaveId = ref<number | ''>('')
const plan = ref<Plan | null>(null)
const checkedKeys = ref<string[]>([])
const submitResult = ref<SubmitResult | null>(null)
const dispatchError = ref('')
const loadingWaves = ref(false)
const generating = ref(false)
const submitting = ref(false)

const validCandidates = computed(() => (plan.value?.candidates ?? []).filter((item) => item.valid))
const allValidChecked = computed(
  () => validCandidates.value.length > 0 && checkedKeys.value.length === validCandidates.value.length,
)

async function openDispatch() {
  dispatchVisible.value = true
  dispatchError.value = ''
  plan.value = null
  submitResult.value = null
  checkedKeys.value = []
  if (!waves.value.length) {
    loadingWaves.value = true
    try {
      const response = await request(`${ENDPOINT}/waves`)
      if (!response.ok) throw new Error('航班波次读取失败')
      waves.value = await response.json()
    } catch (error) {
      dispatchError.value = error instanceof Error ? error.message : '航班波次读取失败'
    } finally {
      loadingWaves.value = false
    }
  }
}

function closeDispatch() {
  dispatchVisible.value = false
}

async function generatePlan() {
  if (!selectedWaveId.value) return
  dispatchError.value = ''
  submitResult.value = null
  checkedKeys.value = []
  generating.value = true
  try {
    const response = await request(`${ENDPOINT}/dispatch/plan`, {
      method: 'POST',
      body: JSON.stringify({ wave_id: selectedWaveId.value }),
    })
    if (!response.ok) {
      throw new Error('派车方案生成失败，请稍后重试')
    }
    plan.value = await response.json()
    checkedKeys.value = validCandidates.value.map((item) => item.item_key)
  } catch (error) {
    dispatchError.value = error instanceof Error ? error.message : '派车方案生成失败'
  } finally {
    generating.value = false
  }
}

function toggleAllValid(event: Event) {
  checkedKeys.value = (event.target as HTMLInputElement).checked
    ? validCandidates.value.map((item) => item.item_key)
    : []
}

async function submitPlan() {
  if (!plan.value || !checkedKeys.value.length) return
  dispatchError.value = ''
  const chosen = new Set(checkedKeys.value)
  const items = plan.value.candidates
    .filter((item) => item.valid && chosen.has(item.item_key))
    .map((item) => ({
      item_key: item.item_key,
      关联航班: item.关联航班,
      乘客人数: item.乘客人数,
      出发时刻: item.出发时刻,
      到达时刻: item.到达时刻,
      车辆编号: item.车辆编号,
    }))
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/dispatch/submit`, {
      method: 'POST',
      body: JSON.stringify({ wave_id: plan.value.wave.id, items }),
    })
    if (!response.ok) {
      throw new Error('派车提交失败，请稍后重试')
    }
    const result: SubmitResult = await response.json()
    submitResult.value = result
    const createdKeys = new Set(result.created.map((entry) => String(entry.item_key)))
    checkedKeys.value = checkedKeys.value.filter((key) => !createdKeys.has(key))
    await reload()
  } catch (error) {
    dispatchError.value = error instanceof Error ? error.message : '派车提交失败'
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.filter-item select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 13px;
  min-width: 130px;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 920px;
  max-width: 94vw;
  max-height: 88vh;
  background: #fff;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.modal-head h3 { margin: 0; font-size: 15px; }
.modal-body { padding: 12px 16px; overflow: auto; }
.modal-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  border-top: 1px solid var(--border);
}
.wave-bar {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  margin-bottom: 10px;
}
.plan-summary {
  display: flex;
  gap: 14px;
  font-size: 13px;
  margin-bottom: 8px;
}
.plan-table .col-check { width: 42px; }
.row-invalid { background: #fef3f2; }
.ok-text { color: #067647; }
.muted-text { color: var(--muted); font-size: 12px; }
.submit-result {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
}
.submit-result p { margin: 0 0 6px; font-weight: 600; }
.result-list { margin: 4px 0; padding-left: 18px; }
.result-list li { margin: 2px 0; }
</style>
