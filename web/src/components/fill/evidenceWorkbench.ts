import type { FillRunEvidenceBlock, FillRunEvidenceField, FillRunEvidenceRef, FillRunWritebackBlock } from '@/api/types'

export type FieldFilter = 'all' | 'confirmed' | 'uncertain' | 'flagged' | 'not_found' | 'failed'

export interface WorkbenchField extends FillRunEvidenceField {
  key: string
  label: string
  category: Exclude<FieldFilter, 'all'> | 'other'
  evidence_refs: FillRunEvidenceRef[]
  legacy: boolean
}

export function normalizeFields(evidence?: FillRunEvidenceBlock, writeback?: FillRunWritebackBlock): WorkbenchField[] {
  const current = evidence?.fields ?? []
  const legacy = current.length === 0
  const fields: FillRunEvidenceField[] = legacy
    ? (writeback?.fields ?? []).map((field) => ({
      ...field,
      writeback_status: field.status,
      question_text: field.field_key || field.field_id,
      target_cell: field.target_cell || field.cell,
    }))
    : current
  return fields.map((field, index) => ({
    ...field,
    key: `${field.field_id || field.field_key || field.target_cell || 'field'}:${index}`,
    label: field.question_text || field.field_key || field.field_id || `字段 ${index + 1}`,
    category: fieldCategory(field),
    evidence_refs: field.evidence_refs ?? [],
    legacy,
  }))
}

export function isFailedField(field: FillRunEvidenceField): boolean {
  return field.answer_status === 'failed' || field.writeback_status === 'failed'
    || Boolean(field.error_code)
    || (field.reasons ?? []).some((reason) => ['failed_field', 'field_failed', 'field_exception'].includes(reason))
}

function fieldCategory(field: FillRunEvidenceField): WorkbenchField['category'] {
  if (isFailedField(field)) return 'failed'
  if (field.writeback_status === 'flagged') return 'flagged'
  if (field.answer_status === 'not_found' || field.writeback_status === 'not_found') return 'not_found'
  if (field.writeback_status === 'confirmed') return 'confirmed'
  if (field.writeback_status === 'uncertain' || ['partial_clue', 'conflict_unresolved'].includes(field.answer_status || '')) return 'uncertain'
  return 'other'
}

export function displayAnswer(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未记录答案'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function targetLocation(field: FillRunEvidenceField): string {
  const cell = field.target_cell || ''
  if (cell.includes('!')) return cell
  return [field.sheet_name, cell].filter(Boolean).join('!') || (field.row_index ? `第 ${field.row_index} 行` : '未记录目标单元格')
}

export function filterFields(fields: WorkbenchField[], search: string, category: FieldFilter): WorkbenchField[] {
  const needle = search.trim().toLocaleLowerCase()
  return fields.filter((field) => (
    category === 'all'
    || (category === 'not_found' ? field.answer_status === 'not_found' || field.writeback_status === 'not_found'
      : category === 'failed' ? isFailedField(field)
        : field.writeback_status ? field.writeback_status === category : field.category === category)
  ) && (!needle || [
    field.label, field.field_id, field.field_key, targetLocation(field), displayAnswer(field.answer_value),
  ].filter(Boolean).join(' ').toLocaleLowerCase().includes(needle)))
}

export function evidenceSource(ref: FillRunEvidenceRef): string {
  return ref.file_name || ref.document_id || ref.object_key || ref.chunk_id || '未记录来源名称'
}

export function evidenceLocation(ref: FillRunEvidenceRef): string {
  return [ref.source_anchor, ref.sheet_name, ref.cell_range || ref.cell,
    ref.table_index !== null && ref.table_index !== undefined ? `表 ${ref.table_index}` : '',
    ref.row_index !== null && ref.row_index !== undefined && ((!ref.cell_range && !ref.cell) || (ref.table_index !== null && ref.table_index !== undefined)) ? `行 ${ref.row_index}` : '',
    ref.paragraph_index !== null && ref.paragraph_index !== undefined ? `段落 ${ref.paragraph_index}` : '',
    ref.page !== null && ref.page !== undefined && ref.page !== '' ? `第 ${ref.page} 页` : '']
    .filter(Boolean).join(' · ') || '未记录来源位置'
}

// Python provenance offsets count Unicode code points; JS string offsets count UTF-16 units.
// Validate again before rendering. A server label never authorizes arbitrary HTML or a guessed span.
export function highlightParts(ref: FillRunEvidenceRef): { before: string; quote: string; after: string; valid: boolean } {
  const provenance = ref.provenance
  const source = provenance?.source_text ?? ''
  const points = Array.from(source)
  const start = provenance?.start
  const end = provenance?.end
  const quote = provenance?.quote ?? ''
  const valid = provenance?.match_status === 'exact' && quote.length > 0
    && typeof start === 'number' && Number.isInteger(start)
    && typeof end === 'number' && Number.isInteger(end)
    && start >= 0 && end > start && end <= points.length
    && points.slice(start, end).join('') === quote
  return valid
    ? { before: points.slice(0, start).join(''), quote, after: points.slice(end).join(''), valid: true }
    : { before: source || ref.text_preview || '', quote: '', after: '', valid: false }
}

const fieldStatusLabels: Record<string, string> = {
  confirmed: '确认字段', uncertain: '存疑字段', flagged: '已标记', not_found: '未找到', failed: '处理失败',
  answered: '已回答', partial_clue: '部分线索', conflict_unresolved: '来源冲突',
}

export function fieldStatus(field: FillRunEvidenceField): string {
  const status = field.writeback_status || field.answer_status || ''
  return fieldStatusLabels[status] || status || '未记录状态'
}

export function answerStatus(status?: string): string {
  return fieldStatusLabels[status || ''] || status || '未记录回答状态'
}

const actionLabels: Record<string, string> = {
  written: '已写入', written_red_comment: '已标红写入，需复核', review_only: '仅进入复核清单',
  skipped_uncertain_policy: '按存疑策略跳过', skipped_non_empty_cell: '保留原有单元格内容',
  skipped_formula: '保留公式，未写入', invalid_cell: '目标单元格无效',
  preserved_existing: '保留原有单元格内容',
  duplicate_target_cell: '目标单元格重复，未写入', skipped_status: '按回答状态跳过',
}

export function writebackAction(action?: string): string {
  return actionLabels[action || ''] || action || '未记录实际写回动作'
}

const reasonLabels: Record<string, string> = {
  written: '已按写回策略写入', written_uncertain: '已按存疑策略标红写入，需人工复核',
  uncertain_written: '已按存疑策略标红写入，需人工复核', review_only: '仅进入复核清单，未自动写入',
  skipped_uncertain_policy: '存疑字段按配置跳过写入', low_confidence: '答案置信度不足，需人工复核',
  not_found_with_relevant_hits: '召回了相关线索，尚不足以回答',
  not_found_with_target_main_fact: '目标机房资料存在相关线索，尚不足以回答',
  quote_located: '引用在源文本中唯一定位', quote_not_unique: '引用在源文本中出现多次，未能消歧',
  quote_not_found: '候选引用未匹配源文本', chunk_not_retrieved: '引用片段不在本次检索结果中',
  missing_chunk_id: '未提供来源片段编号', missing_source_text: '未归档可核对的源文本',
  missing_quote: '未提供可定位的逐字引用', provenance_checkpoint_missing: '历史检查点没有原文定位记录',
  source_hash_mismatch: '源文本哈希校验未通过', invalid_span: '引用区间校验未通过',
  no_source_chunk_ids: '没有直接来源引用', answered_without_source: '答案没有引用来源',
  invalid_source_reference: '引用来源无效', cited_source_not_in_retrieved_hits: '引用不在本次检索结果中',
  evidence_strength_below_writeback_threshold: '证据强度未达到写回阈值',
  weak_evidence_for_writeback: '直接证据不足，需复核', unsupported_by_strong_evidence: '答案缺少充分的直接证据',
  field_mismatch: '证据字段与目标字段不一致', scope_mismatch: '证据范围与目标机房或设备不一致',
  status_mismatch: '证据中的现状或规划状态不一致', slot_mismatch: '组合字段的必需槽不完整',
  unit_mismatch: '答案与证据单位不一致', answer_evidence_mismatch: '答案与证据内容不一致',
  conflict_unresolved: '来源冲突尚未解决', missing_numeric_or_unit_support: '数值或单位缺少直接证据',
  skipped_formula: '目标单元格含公式，保持原值', duplicate_target_cell: '多个字段指向同一目标单元格',
  invalid_cell: '目标单元格位置无效', skipped_non_empty_cell: '目标单元格已有内容，保持原值',
  target_non_empty: '目标单元格已有人工值，保持原值并进入复核',
  failed_field: '字段处理失败，需重新运行或人工补充', field_failed: '字段处理失败，需重新运行或人工补充',
  field_exception: '字段处理发生异常，需重新运行或人工补充',
}

export function reasonLabel(reason: string): string {
  return (Object.hasOwn(reasonLabels, reason) ? reasonLabels[reason] : undefined)
    || (/[\u4e00-\u9fff]/.test(reason) ? reason : '其他归档原因')
}

export function isKnownReason(reason: string): boolean {
  return Object.hasOwn(reasonLabels, reason) || /[\u4e00-\u9fff]/.test(reason)
}

export function provenanceLabel(ref: FillRunEvidenceRef): string {
  if (!ref.provenance) return '没有原文定位记录'
  if (ref.provenance.match_status === 'exact') return highlightParts(ref).valid ? '引用定位成功' : '定位记录校验未通过'
  return ({ ambiguous: '多处匹配，待消歧', unmatched: '未匹配原文', unavailable: '无法定位引用' } as Record<string, string>)[ref.provenance.match_status] || '未知定位状态'
}
