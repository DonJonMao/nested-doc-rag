<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowUpRight, FileSearch, Image, Search, ShieldCheck } from 'lucide-vue-next'
import type { FillRunEvidenceBlock, FillRunEvidenceRef, FillRunWritebackBlock } from '@/api/types'
import {
  answerStatus, displayAnswer, evidenceLocation, evidenceSource, fieldStatus, filterFields, highlightParts,
  isKnownReason, normalizeFields, provenanceLabel, reasonLabel, targetLocation, writebackAction,
  type FieldFilter,
} from './evidenceWorkbench'

const props = defineProps<{
  evidence?: FillRunEvidenceBlock
  writeback?: FillRunWritebackBlock
  runStatus: string
  artifactInvalid?: boolean
}>()
const emit = defineEmits<{ 'download-image': [objectKey: string] }>()
const search = ref('')
const filter = ref<FieldFilter>('all')
const selectedKey = ref('')
const list = ref<HTMLElement | null>(null)
const fields = computed(() => normalizeFields(props.evidence, props.writeback))
const visible = computed(() => filterFields(fields.value, search.value, filter.value))
const selected = computed(() => visible.value.find((field) => field.key === selectedKey.value) ?? visible.value[0])
const selectedReasons = computed(() => [...new Set((selected.value?.reasons ?? []).map(reasonLabel))])
const unknownReasons = computed(() => (selected.value?.reasons ?? []).filter((reason) => !isKnownReason(reason)))
const isProcessing = computed(() => ['created', 'queued', 'running', 'cancel_requested'].includes(props.runStatus))
const hasProvenance = computed(() => (props.evidence?.fields?.length ?? 0) > 0)
const selectedRefs = computed(() => (selected.value?.evidence_refs ?? []).map((source, index) => ({
  source, key: `${source.chunk_id || source.document_id || 'reference'}:${index}`, parts: highlightParts(source),
})))
const categories: { value: FieldFilter; label: string }[] = [
  { value: 'all', label: '全部状态' }, { value: 'confirmed', label: '确认字段' },
  { value: 'uncertain', label: '存疑字段' }, { value: 'flagged', label: '已标记' },
  { value: 'not_found', label: '未找到' }, { value: 'failed', label: '处理失败' },
]

watch(visible, (items) => {
  if (!items.some((field) => field.key === selectedKey.value)) selectedKey.value = items[0]?.key ?? ''
}, { immediate: true })

async function navigate(event: KeyboardEvent, index: number) {
  const offsets: Record<string, number> = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: visible.value.length - 1 }
  if (!(event.key in offsets)) return
  event.preventDefault()
  const next = Math.max(0, Math.min(visible.value.length - 1, offsets[event.key]))
  const field = visible.value[next]
  if (!field) return
  selectedKey.value = field.key
  await nextTick()
  list.value?.querySelectorAll<HTMLButtonElement>('.evidence-workbench__field')[next]?.focus()
}

function textSpace(ref: FillRunEvidenceRef) {
  return ref.provenance?.text_space === 'raw_source_text' ? '来源原文'
    : ref.provenance?.text_space === 'raw_text' ? '检索证据文本（含解析说明）' : '参考摘录'
}
</script>

<template>
  <section class="evidence-workbench gk-card" aria-labelledby="evidence-workbench-title">
    <header class="evidence-workbench__header">
      <div>
        <div class="evidence-workbench__eyebrow"><FileSearch :size="16" aria-hidden="true" /> FIELD EVIDENCE</div>
        <h2 id="evidence-workbench-title" class="gk-card-title">证据工作台</h2>
        <p class="gk-caption">查看字段答案、实际写回与来源。引用定位成功仅表示引用存在于源文本中。</p>
      </div>
      <div v-if="hasProvenance && evidence" class="evidence-workbench__summary" aria-label="归档引用定位统计">
        <span><b>{{ evidence.summary.exact }}</b> 定位成功</span>
        <span><b>{{ evidence.summary.ambiguous }}</b> 多处匹配</span>
        <span><b>{{ evidence.summary.unmatched }}</b> 未匹配</span>
        <span><b>{{ evidence.summary.unavailable }}</b> 无法定位</span>
        <small>统计单位：引用</small>
      </div>
    </header>

    <p v-if="artifactInvalid" class="evidence-workbench__notice evidence-workbench__notice--warning">归档结果未通过校验，请勿据此填写；查看任务错误信息后重新运行。</p>
    <p v-if="fields.length && !hasProvenance" class="evidence-workbench__notice">此结果没有原文定位记录。以下来源摘录用于人工复核，不能视为已通过原文区间校验。</p>

    <div v-if="fields.length" class="evidence-workbench__body">
      <aside class="evidence-workbench__sidebar" aria-label="字段检索与选择">
        <div class="evidence-workbench__controls">
          <label class="evidence-workbench__search">
            <Search :size="16" aria-hidden="true" />
            <input v-model="search" type="search" aria-label="搜索字段、单元格或答案" placeholder="搜索字段、单元格或答案" />
          </label>
          <label class="evidence-workbench__filter">
            <span class="evidence-workbench__sr-only">按字段状态筛选</span>
            <select v-model="filter" aria-label="按字段状态筛选">
              <option v-for="category in categories" :key="category.value" :value="category.value">{{ category.label }}</option>
            </select>
          </label>
          <p class="gk-caption" role="status" aria-live="polite">显示 {{ visible.length }} / {{ fields.length }} 个字段</p>
        </div>
        <div ref="list" class="evidence-workbench__list" aria-label="字段列表">
          <button
            v-for="(field, index) in visible" :key="field.key" type="button"
            class="evidence-workbench__field" :class="{ 'is-selected': selected?.key === field.key }"
            :tabindex="selected?.key === field.key ? 0 : -1"
            :aria-pressed="selected?.key === field.key" aria-controls="evidence-workbench-detail"
            @click="selectedKey = field.key" @keydown="navigate($event, index)"
          >
            <span class="evidence-workbench__field-content"><strong>{{ field.label }}</strong><small>{{ targetLocation(field) }}</small></span>
            <span class="evidence-workbench__status" :class="`evidence-workbench__status--${field.category}`">{{ fieldStatus(field) }}</span>
          </button>
          <p v-if="!visible.length" class="evidence-workbench__empty">没有符合筛选条件的字段。请调整关键词或状态。</p>
        </div>
        <p class="evidence-workbench__keyboard gk-caption">方向键切换字段 · Home / End 跳转</p>
      </aside>

      <article id="evidence-workbench-detail" class="evidence-workbench__detail" aria-label="所选字段答案与证据">
        <template v-if="selected">
          <div class="evidence-workbench__detail-head">
            <div><p class="gk-caption">{{ targetLocation(selected) }}</p><h3>{{ selected.label }}</h3></div>
            <span class="evidence-workbench__status" :class="`evidence-workbench__status--${selected.category}`">{{ fieldStatus(selected) }}</span>
          </div>
          <div class="evidence-workbench__answer">
            <span class="gk-caption">原始答案 · {{ answerStatus(selected.answer_status) }}</span>
            <p>{{ displayAnswer(selected.answer_value) }}</p>
          </div>
          <section v-if="selected.acquisition?.strategy === 'sufficiency_guided'" class="evidence-workbench__decision" aria-label="检索路径">
            <FileSearch :size="18" aria-hidden="true" />
            <div>
              <p><strong>检索路径</strong> {{ selected.acquisition.acquisition_rounds }} 轮 · {{ selected.acquisition.qdrant_query_calls ?? '未记录' }} 次检索</p>
              <ul v-if="Array.isArray(selected.acquisition.rounds)">
                <li v-for="round in selected.acquisition.rounds" :key="round.retrieval_round">
                  {{ round.retrieval_round === 0 ? '首轮' : '补充检索' }}：{{ round.hit_count ?? '未记录' }} 条证据
                  <span v-if="round.missing_facts?.length"> · 缺失事实：{{ round.missing_facts.join('、') }}</span>
                  <span v-if="round.evidence_gain !== undefined"> · 新增 {{ round.evidence_gain }} 条</span>
                </li>
              </ul>
              <p v-if="selected.acquisition.final_sufficiency" class="gk-caption">{{ selected.acquisition.final_sufficiency.sufficient ? '证据检查：充足' : '证据检查：仍不足' }} · {{ selected.acquisition.final_sufficiency.reason }}</p>
            </div>
          </section>
          <section class="evidence-workbench__decision" aria-label="写回判定">
            <ShieldCheck :size="18" aria-hidden="true" />
            <div>
              <p><strong>实际写回动作</strong> {{ writebackAction(selected.writeback_action) }}</p>
              <p v-if="selected.existing_value_policy" class="gk-caption">策略：{{ selected.existing_value_policy === 'preserve' ? '保留已有内容' : selected.existing_value_policy }} · 原值：{{ selected.old_value == null || selected.old_value === '' ? '空' : displayAnswer(selected.old_value) }} · 写回后值：{{ displayAnswer(selected.new_value) }}</p>
              <p class="gk-caption">{{ selected.writeback_allowed === true ? '门控允许写回' : selected.writeback_allowed === false ? '门控未允许写回' : '未记录门控许可' }} · 引用定位不会改变此判定</p>
              <ul v-if="selectedReasons.length"><li v-for="(reason, index) in selectedReasons" :key="index">{{ reason }}</li></ul>
              <p v-else class="gk-caption">没有归档门控理由。</p>
              <details v-if="unknownReasons.length" class="evidence-workbench__metadata"><summary>归档诊断详情</summary><ul><li v-for="(reason, index) in unknownReasons" :key="index">{{ reason }}</li></ul></details>
            </div>
          </section>

          <section class="evidence-workbench__sources" aria-labelledby="evidence-source-title">
            <h4 id="evidence-source-title">来源与原文 <span>{{ selectedRefs.length }} 个引用</span></h4>
            <p v-if="!selectedRefs.length" class="evidence-workbench__empty">{{ selected.answer_status === 'not_found' ? '当前检索未找到可用直接证据。' : '此字段没有归档来源引用，请结合写回判定人工核对。' }}</p>
            <article v-for="ref in selectedRefs" :key="ref.key" class="evidence-workbench__source">
              <div class="evidence-workbench__source-head">
                <div><strong>{{ evidenceSource(ref.source) }}</strong><p class="gk-caption">{{ evidenceLocation(ref.source) }}</p></div>
                <span class="evidence-workbench__match" :class="{ 'is-exact': ref.parts.valid }">{{ provenanceLabel(ref.source) }}</span>
              </div>
              <p class="evidence-workbench__text-label gk-caption">{{ textSpace(ref.source) }}</p>
              <blockquote v-if="ref.parts.before || ref.parts.quote || ref.parts.after" class="evidence-workbench__quote">{{ ref.parts.before }}<mark v-if="ref.parts.valid">{{ ref.parts.quote }}</mark>{{ ref.parts.after }}</blockquote>
              <p v-else class="gk-caption">没有可显示的来源文本。</p>
              <p v-if="ref.source.provenance?.quote && !ref.parts.valid" class="evidence-workbench__candidate">候选引用：{{ ref.source.provenance.quote }}</p>
              <p v-if="ref.parts.valid" class="gk-caption">引用区间 [{{ ref.source.provenance?.start }}, {{ ref.source.provenance?.end }}) · 仅校验逐字定位</p>
              <p v-else-if="ref.source.provenance?.match_status === 'exact'" class="evidence-workbench__invalid">引用区间与当前源文本不一致，未高亮。</p>
              <p v-else-if="ref.source.provenance?.reason" class="gk-caption">{{ reasonLabel(ref.source.provenance.reason) }}</p>
              <details v-if="ref.source.provenance" class="evidence-workbench__metadata">
                <summary>来源定位信息</summary>
                <dl><dt>文本空间</dt><dd>{{ ref.source.provenance.text_space || '未记录' }}</dd><dt>索引版本</dt><dd>{{ ref.source.provenance.index_version || '未记录' }}</dd><dt>源文本 SHA256</dt><dd>{{ ref.source.provenance.source_text_hash || '未记录' }}</dd><dt>来源片段</dt><dd>{{ ref.source.chunk_id || '未记录' }}</dd><dt>定位诊断</dt><dd>{{ ref.source.provenance.reason || '未记录' }}</dd></dl>
              </details>
              <button v-if="ref.source.image_object_key" type="button" class="evidence-workbench__download" @click="emit('download-image', ref.source.image_object_key)"><Image :size="15" aria-hidden="true" /> 下载图片证据 <ArrowUpRight :size="14" aria-hidden="true" /></button>
            </article>
          </section>
        </template>
        <p v-else class="evidence-workbench__empty">选择一个字段以查看答案和证据。</p>
      </article>
    </div>
    <div v-else class="evidence-workbench__empty evidence-workbench__empty--run" role="status">
      <FileSearch :size="26" aria-hidden="true" />
      <strong>{{ isProcessing ? '字段证据尚未归档' : '没有可查看的字段记录' }}</strong>
      <p>{{ isProcessing ? '任务处理中。结果完成并通过归档校验后，可在此查看字段答案与来源。' : ['failed', 'canceled', 'cancelled'].includes(runStatus) ? '任务未产生可用的归档字段记录，请查看下方执行事件与任务错误信息。' : '当前结果未包含字段证据或旧版写回记录。可下载已有产物，或使用新的配置重新运行。' }}</p>
    </div>
  </section>
</template>

<style scoped>
.evidence-workbench { min-width: 0; }
.evidence-workbench__header { display: flex; justify-content: space-between; align-items: start; gap: 24px; flex-wrap: wrap; padding: 24px; border-bottom: 1px solid var(--gk-glass-line); }
.evidence-workbench__header .gk-caption { margin: 9px 0 0; max-width: 600px; }
.evidence-workbench__eyebrow { display: flex; align-items: center; gap: 7px; color: var(--gk-info); font-size: 11px; font-weight: 600; letter-spacing: .1em; margin-bottom: 9px; }
.evidence-workbench__summary { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 12px; color: var(--gk-ink-3); max-width: 330px; padding-top: 6px; }
.evidence-workbench__summary b { color: var(--gk-ink-1); font-size: 16px; font-variant-numeric: tabular-nums; margin-right: 3px; }
.evidence-workbench__summary small { width: 100%; }
.evidence-workbench__notice { margin: 0; padding: 12px 24px; color: var(--gk-ink-2); background: var(--gk-page-soft); border-bottom: 1px solid var(--gk-glass-line); font-size: 13px; line-height: 1.6; }
.evidence-workbench__notice--warning { color: var(--gk-danger); }
.evidence-workbench__body { display: grid; grid-template-columns: minmax(250px, .8fr) minmax(0, 1.7fr); }
.evidence-workbench__sidebar { min-width: 0; border-right: 1px solid var(--gk-glass-line); background: var(--gk-glass-bg-soft); }
.evidence-workbench__controls { display: grid; gap: 10px; padding: 20px 16px 14px; }
.evidence-workbench__controls p { margin: 0; }
.evidence-workbench__search { display: flex; align-items: center; gap: 8px; padding: 0 10px; border: 1px solid var(--gk-hairline-soft); border-radius: var(--gk-radius-sm); color: var(--gk-ink-3); background: var(--gk-white); }
.evidence-workbench__search:focus-within { box-shadow: var(--gk-focus-ring); border-color: var(--gk-info); }
.evidence-workbench__search input { min-width: 0; width: 100%; border: 0; padding: 10px 0; outline: 0; color: var(--gk-ink-1); background: transparent; font-size: 13px; }
.evidence-workbench__filter select { width: 100%; min-height: 38px; padding: 7px 9px; color: var(--gk-ink-2); background: var(--gk-white); border: 1px solid var(--gk-hairline-soft); border-radius: var(--gk-radius-sm); font-size: 13px; }
.evidence-workbench__list { max-height: 560px; overflow-y: auto; padding: 0 9px 9px; }
.evidence-workbench__field { width: 100%; min-height: 68px; display: flex; align-items: start; gap: 12px; justify-content: space-between; padding: 13px 10px; text-align: left; border: 1px solid transparent; border-radius: var(--gk-radius-sm); background: transparent; color: var(--gk-ink-1); cursor: pointer; }
.evidence-workbench__field:hover { background: var(--gk-page); }
.evidence-workbench__field.is-selected { background: rgba(0, 102, 204, .065); border-color: rgba(0, 102, 204, .16); }
.evidence-workbench__field-content { min-width: 0; display: grid; gap: 5px; }
.evidence-workbench__field-content strong { font-size: 13px; font-weight: 600; line-height: 1.5; overflow-wrap: anywhere; }
.evidence-workbench__field-content small { color: var(--gk-ink-3); font-size: 12px; overflow-wrap: anywhere; }
.evidence-workbench__status { display: inline-flex; flex-shrink: 0; border-radius: var(--gk-radius-pill); padding: 3px 7px; font-size: 11px; color: var(--gk-ink-3); background: var(--gk-page); white-space: nowrap; }
.evidence-workbench__status--confirmed { color: var(--gk-success); background: rgba(29, 127, 67, .08); }
.evidence-workbench__status--uncertain { color: var(--gk-warning); background: rgba(179, 107, 0, .08); }
.evidence-workbench__status--flagged, .evidence-workbench__status--failed { color: var(--gk-danger); background: rgba(201, 52, 43, .07); }
.evidence-workbench__keyboard { padding: 8px 19px 16px; margin: 0; font-size: 11px; }
.evidence-workbench__detail { min-width: 0; padding: 24px; }
.evidence-workbench__detail-head { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.evidence-workbench__detail-head h3 { margin: 6px 0 0; font-size: 19px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.evidence-workbench__detail-head p { margin: 0; }
.evidence-workbench__answer { padding: 23px 0 18px; }
.evidence-workbench__answer p { margin: 8px 0 0; font-size: 19px; font-weight: 500; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-workbench__decision { display: flex; gap: 10px; padding: 14px; background: var(--gk-page-soft); border: 1px solid var(--gk-hairline-soft); border-radius: var(--gk-radius-md); font-size: 13px; line-height: 1.6; }
.evidence-workbench__decision > svg { flex-shrink: 0; color: var(--gk-info); margin-top: 2px; }
.evidence-workbench__decision p { margin: 0 0 5px; }
.evidence-workbench__decision strong { margin-right: 6px; font-weight: 600; }
.evidence-workbench__decision ul { padding-left: 17px; margin: 8px 0 0; color: var(--gk-ink-2); overflow-wrap: anywhere; }
.evidence-workbench__sources { margin-top: 25px; }
.evidence-workbench__sources h4 { margin: 0 0 13px; font-size: 14px; font-weight: 600; }
.evidence-workbench__sources h4 span { margin-left: 6px; color: var(--gk-ink-3); font-size: 12px; font-weight: 400; }
.evidence-workbench__source { padding: 18px 0; border-top: 1px solid var(--gk-glass-line); }
.evidence-workbench__source-head { display: flex; align-items: start; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.evidence-workbench__source-head strong { display: block; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.evidence-workbench__source-head p { margin: 5px 0 0; overflow-wrap: anywhere; }
.evidence-workbench__match { color: var(--gk-ink-3); border: 1px solid var(--gk-hairline-soft); border-radius: var(--gk-radius-xs); padding: 3px 7px; font-size: 11px; }
.evidence-workbench__match.is-exact { color: var(--gk-info); border-color: rgba(0, 102, 204, .2); }
.evidence-workbench__text-label { margin: 14px 0 7px; }
.evidence-workbench__quote { max-height: 330px; overflow-y: auto; margin: 0 0 10px; padding: 13px 15px; border-left: 3px solid var(--gk-info); background: var(--gk-page-soft); color: var(--gk-ink-2); border-radius: 0 var(--gk-radius-sm) var(--gk-radius-sm) 0; font-size: 13px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-workbench__quote mark { color: var(--gk-info); background: rgba(0, 102, 204, .11); padding: 1px 0; }
.evidence-workbench__source > .gk-caption { margin: 9px 0; }
.evidence-workbench__candidate { margin: 9px 0; color: var(--gk-ink-3); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-workbench__invalid { font-size: 12px; color: var(--gk-danger); }
.evidence-workbench__metadata { color: var(--gk-ink-3); font-size: 11px; padding-top: 6px; }
.evidence-workbench__metadata summary { cursor: pointer; padding: 3px 0; }
.evidence-workbench__metadata dl { display: grid; grid-template-columns: 95px minmax(0, 1fr); gap: 8px; margin: 10px 0 0; }
.evidence-workbench__metadata dd { margin: 0; overflow-wrap: anywhere; }
.evidence-workbench__download { display: inline-flex; align-items: center; gap: 6px; border: 0; background: transparent; color: var(--gk-info); cursor: pointer; font-size: 12px; padding: 9px 0; margin-top: 6px; }
.evidence-workbench__empty { padding: 20px; margin: 0; color: var(--gk-ink-3); font-size: 13px; line-height: 1.8; }
.evidence-workbench__empty--run { display: grid; justify-items: center; text-align: center; gap: 12px; padding: 48px 24px; }
.evidence-workbench__empty--run strong { color: var(--gk-ink-2); font-weight: 500; }
.evidence-workbench__empty--run p { max-width: 570px; margin: 0; }
.evidence-workbench__sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
.evidence-workbench button:focus-visible, .evidence-workbench select:focus-visible, .evidence-workbench summary:focus-visible { outline: 2px solid var(--gk-info); outline-offset: 2px; box-shadow: var(--gk-focus-ring); }
@media (max-width: 860px) { .evidence-workbench__body { grid-template-columns: minmax(0, 1fr); } .evidence-workbench__sidebar { border-right: 0; border-bottom: 1px solid var(--gk-glass-line); } .evidence-workbench__controls { grid-template-columns: minmax(0, 1fr) 145px; } .evidence-workbench__controls > p { grid-column: 1 / -1; } .evidence-workbench__list { max-height: 265px; } .evidence-workbench__keyboard { display: none; } }
@media (max-width: 480px) { .evidence-workbench__header, .evidence-workbench__detail { padding: 20px 16px; } .evidence-workbench__notice { padding: 12px 16px; } .evidence-workbench__controls { grid-template-columns: minmax(0, 1fr); } .evidence-workbench__field { min-height: 72px; } .evidence-workbench__metadata dl { grid-template-columns: minmax(0, 1fr); gap: 4px; } .evidence-workbench__metadata dd { margin-bottom: 5px; } }
@media (pointer: coarse) { .evidence-workbench__filter select, .evidence-workbench__download { min-height: 44px; } }
</style>
