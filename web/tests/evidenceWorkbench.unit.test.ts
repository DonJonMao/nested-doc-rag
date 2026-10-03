import { describe, expect, it } from 'vitest'
import type { FillRunEvidenceRef } from '../src/api/types'
import { evidenceLocation, filterFields, highlightParts, normalizeFields, provenanceLabel, targetLocation } from '../src/components/fill/evidenceWorkbench'

describe('strict provenance spans', () => {
  const source = '😀：2路市电。'
  it('slices Unicode code points rather than UTF-16 units', () => {
    const ref: FillRunEvidenceRef = { provenance: { match_status: 'exact', quote: '2路市电', start: 2, end: 6, source_text: source } }
    expect(highlightParts(ref)).toEqual({ before: '😀：', quote: '2路市电', after: '。', valid: true })
  })
  it.each([
    { start: 1, end: 5, quote: '2路市电' }, { start: -1, end: 4, quote: '2路市电' },
    { start: 2.5, end: 6, quote: '2路市电' }, { start: 2, end: 99, quote: '2路市电' },
    { start: 2, end: 6, quote: '' }, { start: null, end: null, quote: '2路市电' },
  ])('rejects mismatched, missing or invalid ranges: %j', (span) => {
    const ref: FillRunEvidenceRef = { provenance: { match_status: 'exact', source_text: source, ...span } }
    expect(highlightParts(ref).valid).toBe(false)
    expect(provenanceLabel(ref)).toBe('定位记录校验未通过')
  })
  it('never guesses a match for ambiguous quotes or historical previews', () => {
    const ambiguous: FillRunEvidenceRef = { provenance: { match_status: 'ambiguous', quote: '2N', source_text: '配置：2N；规划：2N', start: 3, end: 5 } }
    expect(highlightParts(ambiguous).valid).toBe(false)
    expect(highlightParts({ text_preview: '2N' })).toEqual({ before: '2N', quote: '', after: '', valid: false })
    expect(provenanceLabel({ text_preview: '2N' })).toBe('没有原文定位记录')
  })
})

describe('search and historical result compatibility', () => {
  it('searches questions, answer values and cell addresses without duplicating sheet names', () => {
    const fields = normalizeFields({ summary: { exact: 0, ambiguous: 0, unmatched: 0, unavailable: 0 }, fields: [
      { field_id: 'name', question_text: '机房名称', answer_value: '机房A', writeback_status: 'confirmed' },
      { field_id: 'ups', question_text: 'UPS单台容量', target_cell: '工勘表!D36', writeback_status: 'uncertain' },
    ] })
    expect(filterFields(fields, ' ups ', 'uncertain').map((f) => f.field_id)).toEqual(['ups'])
    expect(filterFields(fields, '机房A', 'all').map((f) => f.field_id)).toEqual(['name'])
    expect(filterFields(fields, 'D36', 'all').map((f) => f.field_id)).toEqual(['ups'])
    expect(targetLocation(fields[1])).toBe('工勘表!D36')
  })
  it('keeps all old fields without inventing provenance or writeback permission', () => {
    const fields = normalizeFields(undefined, {
      summary: { confirmed: 1, uncertain: 1, flagged: 0, written: 1, review: 1 },
      fields: [
        { field_id: 'old-confirmed', status: 'confirmed', answer_value: 'A', cell: 'D4', evidence_refs: [] },
        { field_id: 'old-uncertain', status: 'uncertain', evidence_refs: [{ text_preview: '系统总容量1200kVA' }] },
      ],
    })
    expect(fields).toHaveLength(2)
    expect(fields.every((field) => field.legacy)).toBe(true)
    expect(fields[0].writeback_allowed).toBeUndefined()
    expect(fields[1].evidence_refs[0].provenance).toBeUndefined()
  })
  it('preserves acquisition and audit values without replacing the answer proposal', () => {
    const acquisition = {
      strategy: 'sufficiency_guided', acquisition_rounds: 2, qdrant_query_calls: null,
      rounds: [{ retrieval_round: 0, hit_count: 2 }, { retrieval_round: 1, hit_count: 2, evidence_gain: 0, missing_facts: ['柴油储量'] }],
      final_sufficiency: { sufficient: false, missing_facts: ['柴油储量'], reason: '本轮材料仍未记录储量' },
    }
    const [field] = normalizeFields({ summary: { exact: 0, ambiguous: 0, unmatched: 0, unavailable: 0 }, fields: [
      { field_id: 'preserved', answer_value: '600kW', old_value: 0, new_value: 0,
        existing_value_policy: 'preserve', writeback_action: 'skipped_non_empty_cell', acquisition },
    ] })
    expect(field.acquisition).toEqual(acquisition)
    expect(field.old_value).toBe(0)
    expect(field.new_value).toBe(0)
    expect(field.answer_value).toBe('600kW')
    expect(field.existing_value_policy).toBe('preserve')
    expect(field.writeback_action).toBe('skipped_non_empty_cell')
  })
  it('does not invent trace or audit metadata when reading an older evidence artifact', () => {
    const [field] = normalizeFields({ summary: { exact: 0, ambiguous: 0, unmatched: 0, unavailable: 1 }, fields: [
      { field_id: 'historical-evidence', answer_value: '原答案', evidence_refs: [{ text_preview: '旧摘录' }] },
    ] })
    expect(field.acquisition).toBeUndefined()
    expect(field.existing_value_policy).toBeUndefined()
    expect(field.old_value).toBeUndefined()
    expect(field.new_value).toBeUndefined()
  })
})

describe('native and historical evidence addresses', () => {
  it('shows an Excel cell range in preference to its compatibility cell', () => {
    expect(evidenceLocation({ sheet_name: '南 501', cell_range: 'A2:D2', cell: 'B2', page: 3 }))
      .toBe('南 501 · A2:D2 · 第 3 页')
  })
  it('keeps Word table, row and paragraph addresses without requiring an Excel cell', () => {
    expect(evidenceLocation({ table_index: 2, row_index: 4 })).toBe('表 2 · 行 4')
    expect(evidenceLocation({ paragraph_index: 14 })).toBe('段落 14')
  })
  it('retains a known Excel row when an older reference has no cell range', () => {
    expect(evidenceLocation({ sheet_name: '历史设备清单', row_index: 91 })).toBe('历史设备清单 · 行 91')
  })
  it('keeps a recorded source anchor and never turns null dimensions into an address', () => {
    expect(evidenceLocation({ source_anchor: '附录 A', table_index: null, row_index: null, paragraph_index: null, page: null }))
      .toBe('附录 A')
    expect(evidenceLocation({ table_index: null, row_index: null, paragraph_index: null, page: '' }))
      .toBe('未记录来源位置')
  })
})
