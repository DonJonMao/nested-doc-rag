import { afterEach, describe, expect, it } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import EvidenceWorkbench from '../src/components/fill/EvidenceWorkbench.vue'
import type { FillRunEvidenceBlock, FillRunWritebackBlock } from '../src/api/types'

const evidence: FillRunEvidenceBlock = {
  summary: { exact: 1, ambiguous: 0, unmatched: 0, unavailable: 0 },
  fields: [
    {
      field_id: 'power', question_text: '市电进线', answer_value: '2路', answer_status: 'answered',
      writeback_status: 'confirmed', writeback_action: 'written', writeback_allowed: true,
      target_cell: 'D25', sheet_name: 'Sheet1', reasons: [],
      evidence_refs: [{ file_name: 'power.xlsx', image_object_key: 'images/proof.png', provenance: {
        match_status: 'exact', source_text: '😀：2路<script>alert(1)</script>', quote: '2路', start: 2, end: 4, text_space: 'raw_source_text',
      } }],
    },
    {
      field_id: 'ups', question_text: 'UPS 单台额定容量', answer_value: '1200kVA', answer_status: 'partial_clue',
      writeback_status: 'uncertain', writeback_action: 'review_only', writeback_allowed: false,
      target_cell: 'D36', reasons: ['scope_mismatch'], evidence_refs: [],
    },
    { field_id: 'flag', question_text: '冷却冗余', writeback_status: 'flagged', writeback_action: 'review_only', evidence_refs: [] },
    { field_id: 'missing', question_text: '实测压力', answer_status: 'not_found', writeback_status: 'flagged', evidence_refs: [] },
  ],
}
type Props = { runStatus: string; evidence?: FillRunEvidenceBlock; writeback?: FillRunWritebackBlock; artifactInvalid?: boolean }
const cleanups: (() => void)[] = []
function render(overrides: Partial<Props> = {}) {
  const props = reactive<Props>({ runStatus: 'completed', evidence: structuredClone(evidence), ...overrides })
  const downloads: string[] = []
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({ render: () => h(EvidenceWorkbench, { ...props, onDownloadImage: (key: string) => downloads.push(key) }) })
  app.mount(container)
  cleanups.push(() => { app.unmount(); container.remove() })
  return { props, downloads, container, get: <T extends HTMLElement = HTMLElement>(selector: string) => container.querySelector<T>(selector)! }
}
async function setValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  element.value = value
  element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  await nextTick()
}
afterEach(() => { cleanups.splice(0).forEach((cleanup) => cleanup()) })

describe('live evidence workbench interaction', () => {
  it('shows all field outcomes and uses safe text nodes', () => {
    const { container, get } = render()
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(4)
    expect(get('mark').textContent).toBe('2路')
    expect(container.querySelector('script')).toBeNull()
    expect(get('blockquote').textContent).toContain('<script>alert(1)</script>')
    expect(container.textContent).toContain('引用定位成功')
    expect(container.textContent).not.toContain('答案已验证')
    expect(get('.evidence-workbench__decision').textContent).toContain('已写入')
  })

  it('updates selection through search and independently filters raw not-found even if final flagged', async () => {
    const { container, get } = render()
    await setValue(get<HTMLInputElement>('input[type="search"]'), 'UPS')
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(1)
    expect(get('.evidence-workbench__detail h3').textContent).toBe('UPS 单台额定容量')
    expect(get('.evidence-workbench__decision').textContent).toContain('门控未允许写回')
    expect(get('.evidence-workbench__decision').textContent).toContain('仅进入复核清单')
    expect(container.querySelector('mark')).toBeNull()
    await setValue(get<HTMLInputElement>('input'), '')
    await setValue(get<HTMLSelectElement>('select'), 'not_found')
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(1)
    expect(get('.evidence-workbench__detail h3').textContent).toBe('实测压力')
    expect(container.textContent).toContain('当前检索未找到可用直接证据')
    await setValue(get<HTMLInputElement>('input'), '不存在的字段')
    expect(container.textContent).toContain('没有符合筛选条件的字段')
    expect(container.querySelector('.evidence-workbench__detail h3')).toBeNull()
  })

  it('filters failed fields by recorded reasons without misclassifying unresolved conflicts', async () => {
    const data = structuredClone(evidence)
    data.fields[2].answer_status = 'conflict_unresolved'
    data.fields.push({ field_id: 'failed', question_text: '失败字段', answer_status: 'conflict_unresolved', writeback_status: 'flagged', reasons: ['failed_field'] })
    const { container, get } = render({ evidence: data })
    await setValue(get<HTMLSelectElement>('select'), 'failed')
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(1)
    expect(get('.evidence-workbench__detail h3').textContent).toBe('失败字段')
    expect(container.textContent).toContain('字段处理失败，需重新运行或人工补充')
  })

  it('keeps keyboard focus on the selected field with Arrow / Home / End', async () => {
    const { container, get } = render()
    const buttons = container.querySelectorAll<HTMLButtonElement>('.evidence-workbench__field')
    buttons[0].focus()
    buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    await nextTick()
    await nextTick()
    expect(buttons[1].getAttribute('aria-pressed')).toBe('true')
    expect(document.activeElement).toBe(buttons[1])
    buttons[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    await nextTick()
    expect(get('.evidence-workbench__detail h3').textContent).toBe('实测压力')
    buttons[3].dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    await nextTick()
    expect(get('.evidence-workbench__detail h3').textContent).toBe('市电进线')
  })

  it('passes the actual image object key to the existing download callback', () => {
    const { get, downloads } = render()
    get<HTMLButtonElement>('.evidence-workbench__download').click()
    expect(downloads).toEqual(['images/proof.png'])
  })

  it('does not highlight a malformed exact record', () => {
    const broken = structuredClone(evidence)
    broken.fields[0].evidence_refs![0].provenance!.end = 100
    const { container } = render({ evidence: broken })
    expect(container.querySelector('mark')).toBeNull()
    expect(container.textContent).toContain('定位记录校验未通过')
  })

  it('explains common audit reasons and keeps unknown codes in optional diagnostics', () => {
    const data = structuredClone(evidence)
    data.fields[0].reasons = ['written', 'low_confidence', 'new_internal_reason']
    const { get } = render({ evidence: data })
    const reasons = get('.evidence-workbench__decision > div > ul').textContent
    expect(reasons).toContain('已按写回策略写入')
    expect(reasons).toContain('答案置信度不足，需人工复核')
    expect(reasons).toContain('其他归档原因')
    expect(reasons).not.toContain('new_internal_reason')
    expect(get('.evidence-workbench__decision details').textContent).toContain('new_internal_reason')
  })

  it('preserves selection when polling refreshes the result', async () => {
    const { container, props, get } = render()
    container.querySelectorAll<HTMLButtonElement>('.evidence-workbench__field')[2].click()
    await nextTick()
    props.evidence = structuredClone(evidence)
    await nextTick()
    expect(get('.evidence-workbench__detail h3').textContent).toBe('冷却冗余')
  })

  it('renders both acquisition rounds and an unresolved final gap without implying confirmed support', () => {
    const data = structuredClone(evidence)
    data.fields[0].acquisition = {
      strategy: 'sufficiency_guided', acquisition_rounds: 2, qdrant_query_calls: 7,
      rounds: [
        { retrieval_round: 0, hit_count: 2 },
        { retrieval_round: 1, hit_count: 2, missing_facts: ['柴油储量', '油箱位置'], evidence_gain: 0 },
      ],
      final_sufficiency: { sufficient: false, reason: '新增资料仍没有记录柴油储量' },
    }
    const { get } = render({ evidence: data })
    const path = get('section[aria-label="检索路径"]')
    expect(path.textContent).toContain('2 轮 · 7 次检索')
    expect(path.querySelectorAll('li')).toHaveLength(2)
    expect(path.querySelectorAll('li')[0].textContent).toContain('首轮：2 条证据')
    expect(path.querySelectorAll('li')[1].textContent).toContain('补充检索：2 条证据')
    expect(path.textContent).toContain('缺失事实：柴油储量、油箱位置')
    expect(path.textContent).toContain('新增 0 条')
    expect(path.textContent).toContain('证据检查：仍不足')
    expect(path.textContent).toContain('新增资料仍没有记录柴油储量')
    expect(path.textContent).not.toContain('证据检查：充足')
  })

  it('shows a sufficient first round without fabricating a supplementary search', () => {
    const data = structuredClone(evidence)
    data.fields[0].acquisition = {
      strategy: 'sufficiency_guided', acquisition_rounds: 1, qdrant_query_calls: 2,
      rounds: [{ retrieval_round: 0, hit_count: 1 }],
      final_sufficiency: { sufficient: true, reason: '当前机房的字段事实完整' },
    }
    const { get } = render({ evidence: data })
    const path = get('section[aria-label="检索路径"]')
    expect(path.textContent).toContain('1 轮 · 2 次检索')
    expect(path.querySelectorAll('li')).toHaveLength(1)
    expect(path.textContent).toContain('证据检查：充足')
    expect(path.textContent).not.toContain('补充检索')
  })

  it.each([
    { calls: null, label: '未记录 次检索' },
    { calls: undefined, label: '未记录 次检索' },
    { calls: 0, label: '0 次检索' },
  ])('distinguishes missing retrieval counters from a recorded zero: $calls', ({ calls, label }) => {
    const data = structuredClone(evidence)
    data.fields[0].acquisition = { strategy: 'sufficiency_guided', acquisition_rounds: 1, qdrant_query_calls: calls }
    const { get } = render({ evidence: data })
    expect(get('section[aria-label="检索路径"]').textContent).toContain(label)
  })

  it('labels the actual post-policy value separately from the proposed answer when preserving a human value', () => {
    const data = structuredClone(evidence)
    Object.assign(data.fields[0], {
      answer_value: '1200kg', writeback_action: 'skipped_non_empty_cell', existing_value_policy: 'preserve',
      old_value: '人工确认1200kg', new_value: '人工确认1200kg',
    })
    const { get } = render({ evidence: data })
    expect(get('.evidence-workbench__answer p').textContent).toBe('1200kg')
    const decision = get('section[aria-label="写回判定"]').textContent
    expect(decision).toContain('保留原有单元格内容')
    expect(decision).toContain('策略：保留已有内容')
    expect(decision).toContain('原值：人工确认1200kg')
    expect(decision).toContain('写回后值：人工确认1200kg')
    expect(decision).not.toContain('候选值：人工确认1200kg')
  })

  it('renders a numeric zero as an existing value rather than an empty cell', () => {
    const data = structuredClone(evidence)
    Object.assign(data.fields[0], { old_value: 0, new_value: 0, existing_value_policy: 'preserve', writeback_action: 'skipped_non_empty_cell' })
    const { get } = render({ evidence: data })
    expect(get('section[aria-label="写回判定"]').textContent).toContain('原值：0')
    expect(get('section[aria-label="写回判定"]').textContent).not.toContain('原值：空')
  })

  it('renders native Word paragraphs and tables alongside actual Excel ranges', () => {
    const data = structuredClone(evidence)
    data.fields[0].evidence_refs = [
      { file_name: '巡检.docx', paragraph_index: 3, text_preview: 'UPS模块3个' },
      { file_name: '设备.docx', table_index: 2, row_index: 4, text_preview: '机组 / 600kW' },
      { file_name: '参数.xlsx', sheet_name: '南501', cell_range: 'A2:D2', text_preview: '原生参数行' },
    ]
    const { container } = render({ evidence: data })
    const sources = container.querySelectorAll('.evidence-workbench__source-head')
    expect(sources).toHaveLength(3)
    expect(sources[0].textContent).toContain('巡检.docx')
    expect(sources[0].textContent).toContain('段落 3')
    expect(sources[1].textContent).toContain('表 2 · 行 4')
    expect(sources[2].textContent).toContain('南501 · A2:D2')
    expect(container.querySelector('mark')).toBeNull()
  })

  it('treats trace, audit values and source locations as text even when archived strings contain HTML', () => {
    const attack = '<img src=x onerror="globalThis.evidencePwned=1"><svg onload="globalThis.evidencePwned=1">'
    const data = structuredClone(evidence)
    Object.assign(data.fields[0], {
      old_value: attack, new_value: attack, existing_value_policy: 'preserve',
      acquisition: { strategy: 'sufficiency_guided', acquisition_rounds: 2, qdrant_query_calls: 7,
        rounds: [{ retrieval_round: 1, missing_facts: [attack] }], final_sufficiency: { sufficient: false, reason: attack } },
      evidence_refs: [{ file_name: attack, source_anchor: attack, text_preview: '安全预览' }],
    })
    const { container, get } = render({ evidence: data })
    expect(get('section[aria-label="检索路径"]').textContent).toContain(attack)
    expect(get('section[aria-label="写回判定"]').textContent).toContain(attack)
    expect(get('.evidence-workbench__source-head').textContent).toContain(attack)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror], [onload]')).toBeNull()
  })

  it('keeps an older artifact with no acquisition or value policy usable without inventing trace information', () => {
    const { container } = render()
    expect(container.querySelector('section[aria-label="检索路径"]')).toBeNull()
    expect(container.textContent).not.toContain('策略：')
    expect(container.textContent).not.toContain('写回后值：')
    expect(container.textContent).toContain('实际写回动作')
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(4)
  })

  it('shows legacy evidence as unverified previews, including confirmed fields', () => {
    const writeback: FillRunWritebackBlock = {
      summary: { confirmed: 1, uncertain: 0, flagged: 0, written: 1, review: 0 },
      fields: [{ field_id: 'legacy', status: 'confirmed', answer_value: 'A', evidence_refs: [{ text_preview: '机房 A' }] }],
    }
    const { container, get } = render({ evidence: undefined, writeback })
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(1)
    expect(container.textContent).toContain('此结果没有原文定位记录')
    expect(get('blockquote').textContent).toBe('机房 A')
    expect(container.querySelector('mark')).toBeNull()
    expect(container.querySelector('section[aria-label="检索路径"]')).toBeNull()
  })

  it.each(['running', 'failed', 'completed'])('provides an honest empty state for %s runs', (runStatus) => {
    const { container } = render({ evidence: undefined, runStatus })
    expect(container.querySelectorAll('.evidence-workbench__field')).toHaveLength(0)
    expect(container.textContent).toContain(runStatus === 'running' ? '字段证据尚未归档' : '没有可查看的字段记录')
    expect(container.querySelector('.evidence-workbench__summary')).toBeNull()
  })
})
