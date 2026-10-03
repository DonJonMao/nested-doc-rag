package tests

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunEvidenceJSONContract(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	evidence := evidenceTestBlock()
	f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, evidenceTestManifest(f.run.ID, evidence))
	f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+f.run.ID.String(), nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), f.actor))
	f.router().ServeHTTP(rec, req)
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Data formpkg.FillRunDetail `json:"data"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Equal(t, evidence.Summary, body.Data.Evidence.Summary)
	require.Len(t, body.Data.Evidence.Fields, 1)
	field := body.Data.Evidence.Fields[0]
	require.Equal(t, "市电接入情况", field.QuestionText)
	require.Equal(t, "uncertain", field.WritebackStatus)
	require.Equal(t, "review_only", field.WritebackAction)
	require.False(t, field.WritebackAllowed, "locating a quotation must not upgrade the writeback decision")
	require.Equal(t, "双路市电", field.EvidenceRefs[0].Provenance.Quote)
	require.Equal(t, 3, *field.EvidenceRefs[0].Provenance.Start, "offsets count Unicode code points including emoji")
	require.Nil(t, field.EvidenceRefs[1].Provenance.Start, "ambiguous matches cannot expose an arbitrary position")
	require.NotNil(t, field.EvidenceRefs[0].BBox)
	require.NotNil(t, field.EvidenceRefs[0].ProofAttachmentIDs)
	require.NotNil(t, field.EvidenceRefs[0].ProofAttachments)
}

func TestFillRunEvidenceRejectsUntrustedManifest(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any, *pythonpkg.ManifestEvidence)
		suffix string
	}{
		{name: "unsafe artifact", mutate: func(m map[string]any, _ *pythonpkg.ManifestEvidence) {
			m["artifacts"] = map[string]any{artifact.TypeEvidenceProvenance: "../secret.jsonl"}
		}},
		{name: "missing provenance artifact", mutate: func(m map[string]any, _ *pythonpkg.ManifestEvidence) {
			m["artifacts"] = map[string]any{artifact.TypeSummary: "summary.json"}
		}},
		{name: "bad source hash", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance.SourceTextHash = "not-the-source-hash"
		}},
		{name: "wrong quote span", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance.Quote = "另一个答案"
		}},
		{name: "ambiguous position", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[1].Provenance.Start = evidenceInt(0)
		}},
		{name: "incorrect summary", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) { e.Summary.Exact++ }},
		{name: "unknown status", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance.MatchStatus = "proved"
		}},
		{name: "missing provenance", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance = nil
		}},
		{name: "repeated exact quote", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance = evidenceTestProvenance("exact", "双路市电；双路市电", "双路市电", evidenceInt(0), evidenceInt(4))
		}},
		{name: "non-source exact text", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance.TextSpace = "embedding_text"
		}},
		{name: "whitespace exact quote", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].Provenance = evidenceTestProvenance("exact", "a b", " ", evidenceInt(1), evidenceInt(2))
		}},
		{name: "unsafe image key", mutate: func(_ map[string]any, e *pythonpkg.ManifestEvidence) {
			e.Fields[0].EvidenceRefs[0].ImageObjectKey = "runs/../secret.png"
		}},
		{name: "trailing JSON", suffix: " {}"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
			evidence := evidenceTestBlock()
			manifest := evidenceTestManifest(f.run.ID, evidence)
			if tt.mutate != nil {
				tt.mutate(manifest, evidence)
			}
			data, err := json.Marshal(manifest)
			require.NoError(t, err)
			f.addArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, string(data)+tt.suffix)
			f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")
			detail, err := f.service.GetFillRunDetail(context.Background(), f.run.ID, f.actor)
			require.NoError(t, err)
			require.Equal(t, formpkg.ManifestStatusInvalid, detail.ManifestStatus)
			require.Empty(t, detail.Evidence.Fields)
			require.Zero(t, detail.Evidence.Summary.Exact)
		})
	}
}

func TestFillRunEvidenceMissingArchiveAndLegacyFallback(t *testing.T) {
	for _, mode := range []string{"legacy", "missing_manifest", "not_archived"} {
		t.Run(mode, func(t *testing.T) {
			f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
			switch mode {
			case "legacy":
				f.addManifest(map[string]any{artifact.TypeSummary: "summary.json"})
				f.addJSONArtifact(artifact.TypeSummary, "summary.json", map[string]any{})
			case "not_archived":
				f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, evidenceTestManifest(f.run.ID, evidenceTestBlock()))
			}
			detail, err := f.service.GetFillRunDetail(context.Background(), f.run.ID, f.actor)
			require.NoError(t, err)
			require.NotNil(t, detail.Evidence.Fields)
			require.Empty(t, detail.Evidence.Fields)
			data, err := json.Marshal(detail.Evidence)
			require.NoError(t, err)
			require.JSONEq(t, `{"summary":{"exact":0,"ambiguous":0,"unmatched":0,"unavailable":0},"fields":[]}`, string(data))
			if mode == "not_archived" {
				require.Equal(t, formpkg.ArtifactValidationStatusInvalid, detail.ArtifactValidationStatus)
			}
		})
	}
}

func TestFillRunEvidenceOwnerOnly(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, evidenceTestManifest(f.run.ID, evidenceTestBlock()))
	f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")
	for _, role := range []string{auth.RoleOperator, auth.RoleAdmin} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+f.run.ID.String(), nil)
		req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{role}}))
		f.router().ServeHTTP(rec, req)
		require.Equal(t, http.StatusNotFound, rec.Code)
		require.NotContains(t, rec.Body.String(), "双路市电")
	}
}

func TestFillRunEvidenceKeepsPythonEngineRunIDCompatible(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	manifest := evidenceTestManifest(f.run.ID, evidenceTestBlock())
	manifest["run_id"] = "step15_agent_186ff2f25236"
	f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, manifest)
	f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")
	detail, err := f.service.GetFillRunDetail(context.Background(), f.run.ID, f.actor)
	require.NoError(t, err)
	require.Equal(t, formpkg.ManifestStatusValid, detail.ManifestStatus)
	require.Len(t, detail.Evidence.Fields, 1)
}

func TestFillRunEvidenceIgnoresForeignArtifactMetadata(t *testing.T) {
	for _, mode := range []string{"run", "workspace"} {
		t.Run(mode, func(t *testing.T) {
			f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
			f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, evidenceTestManifest(f.run.ID, evidenceTestBlock()))
			f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")
			if mode == "run" {
				f.artifacts.artifacts[0].RunID = uuid.New()
			} else {
				f.artifacts.artifacts[0].WorkspaceID = uuid.New()
			}
			detail, err := f.service.GetFillRunDetail(context.Background(), f.run.ID, f.actor)
			require.NoError(t, err)
			require.Equal(t, formpkg.ManifestStatusMissing, detail.ManifestStatus)
			require.Empty(t, detail.Evidence.Fields)
		})
	}
}

func TestEvidenceImageRequiresOwnedArchivedManifestReference(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	f.addArtifact("evidence_image", "proof.png", "image")
	image := &f.artifacts.artifacts[0]
	image.ContentType = "image/png"
	evidence := evidenceTestBlock()
	evidence.Fields[0].EvidenceRefs[0].ImageObjectKey = image.ObjectKey
	manifest := evidenceTestManifest(f.run.ID, evidence)
	manifest["artifacts"].(map[string]any)["evidence_image"] = "proof.png"
	f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, manifest)
	f.addArtifact(artifact.TypeEvidenceProvenance, "evidence_provenance.jsonl", "{}\n")
	download, err := f.service.DownloadEvidenceImage(context.Background(), f.run.ID, image.ObjectKey, f.actor)
	require.NoError(t, err)
	data, err := io.ReadAll(download.Reader)
	require.NoError(t, err)
	require.NoError(t, download.Reader.Close())
	require.Equal(t, "image", string(data))

	_, err = f.service.DownloadEvidenceImage(context.Background(), f.run.ID, image.ObjectKey, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})
	require.Error(t, err)
	_, err = f.service.DownloadEvidenceImage(context.Background(), f.run.ID, "test/undeclared", f.actor)
	require.Error(t, err)
	_, err = f.service.DownloadEvidenceImage(context.Background(), f.run.ID, "test/../secret", f.actor)
	require.Error(t, err)
}

func TestFillEvidenceWorkerArchiveDetailIntegration(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusQueued)
	runDir := t.TempDir()
	manifestData, err := json.Marshal(evidenceTestManifest(f.run.ID, evidenceTestBlock()))
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(runDir, pythonpkg.RunManifestFilename), manifestData, 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "evidence_provenance.jsonl"), []byte("{\"field_id\":\"field_1\"}\n"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	detail, registered := runEvidenceWorkerFixture(t, f, runDir, manifest)
	require.ElementsMatch(t, []string{artifact.TypeRunManifest, artifact.TypeEvidenceProvenance}, artifactTypesFromRunArtifacts(registered))
	require.Equal(t, "completed", detail.Status)
	require.Equal(t, formpkg.ManifestStatusValid, detail.ManifestStatus)
	require.Equal(t, formpkg.ArtifactValidationStatusValid, detail.ArtifactValidationStatus)
	require.Len(t, detail.Evidence.Fields, 1)
	require.Equal(t, "双路市电", detail.Evidence.Fields[0].EvidenceRefs[0].Provenance.Quote)
}

func TestLegacyWritebackImageReferencesRemainDownloadable(t *testing.T) {
	f := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	f.addArtifact("evidence_image", "proof.png", "legacy image")
	imageKey := f.artifacts.artifacts[0].ObjectKey
	f.addJSONArtifact(artifact.TypeRunManifest, pythonpkg.RunManifestFilename, map[string]any{
		"run_id": "step15_agent_legacy", "status": "completed", "artifacts": map[string]any{"evidence_image": "proof.png"},
		"writeback": map[string]any{"fields": []map[string]any{{"evidence_refs": []map[string]any{{"image_object_key": imageKey}}}}},
	})
	download, err := f.service.DownloadEvidenceImage(context.Background(), f.run.ID, imageKey, f.actor)
	require.NoError(t, err)
	require.NoError(t, download.Reader.Close())
}

// This opt-in test consumes the synthetic output of
// scripts/generate_evidence_contract_fixture.py and writes the actual Go detail
// response for browser contract QA, without making any model or network call.
func TestPythonEvidenceContractFixture(t *testing.T) {
	fixtureDir := os.Getenv("EVIDENCE_CONTRACT_FIXTURE_DIR")
	if fixtureDir == "" {
		t.Skip("EVIDENCE_CONTRACT_FIXTURE_DIR is not set")
	}
	sourceManifest, err := pythonpkg.LoadRunManifestFromDir(fixtureDir)
	require.NoError(t, err)
	f := newFillResultFixture(t, formpkg.FillRunStatusQueued)
	f.run.Name = "匿名资料 · 证据契约验收"
	require.NoError(t, f.runs.Create(context.Background(), f.run))
	runDir := t.TempDir()
	if sourceManifest.WritebackEnabled {
		template, err := os.ReadFile(filepath.Join(fixtureDir, "synthetic_form.xlsx"))
		require.NoError(t, err)
		require.NoError(t, os.WriteFile(filepath.Join(runDir, "synthetic_form.xlsx"), template, 0o644))
	}
	for name := range sourceManifest.Artifacts {
		path, ok := sourceManifest.ArtifactPath(name)
		if !ok {
			continue
		}
		data, err := os.ReadFile(path)
		require.NoError(t, err)
		target := filepath.Join(runDir, sourceManifest.Artifacts[name])
		require.NoError(t, os.MkdirAll(filepath.Dir(target), 0o755))
		require.NoError(t, os.WriteFile(target, data, 0o644))
	}
	data, err := os.ReadFile(filepath.Join(fixtureDir, pythonpkg.RunManifestFilename))
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(runDir, pythonpkg.RunManifestFilename), data, 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	require.Equal(t, sourceManifest.RunID, manifest.RunID, "Python's engine run identity is preserved without rewriting the manifest")
	require.NotEqual(t, f.run.ID.String(), manifest.RunID, "the business fill run identity is an independent UUID")
	detail, registered := runEvidenceWorkerFixture(t, f, runDir, manifest)
	for _, item := range registered {
		require.Equal(t, f.run.ID, item.RunID)
		require.Equal(t, f.workspace, item.WorkspaceID)
		require.Equal(t, f.actor.UserID, item.CreatedBy)
		require.NotEmpty(t, item.SHA256)
	}
	require.Contains(t, artifactTypesFromRunArtifacts(registered), artifact.TypeEvidenceProvenance)
	require.Equal(t, sourceManifest.Evidence.Summary, detail.Evidence.Summary)
	require.Equal(t, len(sourceManifest.Evidence.Fields), len(detail.Evidence.Fields))
	require.Equal(t, formpkg.ManifestStatusValid, detail.ManifestStatus)
	require.Equal(t, formpkg.ArtifactValidationStatusValid, detail.ArtifactValidationStatus)
	data, err = json.MarshalIndent(detail, "", "  ")
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(fixtureDir, "fill_run_detail.json"), append(data, '\n'), 0o644))
}

func runEvidenceWorkerFixture(t *testing.T, f *fillResultFixture, runDir string, manifest *pythonpkg.RunManifest) (*formpkg.FillRunDetail, []artifact.RunArtifact) {
	t.Helper()
	validation, err := pythonpkg.ValidateArtifactsFromManifest(runDir, manifest)
	require.NoError(t, err)
	require.True(t, validation.OK)
	storage := newFakeObjectStorage()
	repo := newFakeArtifactRepo()
	artifactService := artifact.NewService(repo, storage, &fakeAuthorizer{}, nil)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: f.run.ID, OutDir: runDir, Manifest: manifest, Validation: validation}}
	handler := jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(artifactService, nil), nil, zap.NewNop(), jobs.WithFillRunLifecycle(formpkg.NewFillRunLifecycleAdapter(f.runs, nil)))
	job := fillFormWorkerTestJob(f.workspace, f.run.ID, map[string]any{"out_dir": runDir, "writeback": manifest.WritebackEnabled, "template_path": filepath.Join(runDir, "synthetic_form.xlsx")})
	job.CreatedBy = f.actor.UserID
	require.NoError(t, handler.Handle(context.Background(), &job))

	registered, err := artifactService.ListRunArtifacts(context.Background(), f.workspace, f.run.ID, f.actor)
	require.NoError(t, err)
	service := formpkg.NewFillRunService(f.runs, f.forms, &fakeJobUseCase{}, artifactService, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())
	detail, err := service.GetFillRunDetail(context.Background(), f.run.ID, f.actor)
	require.NoError(t, err)
	return detail, registered
}

func evidenceTestManifest(runID uuid.UUID, evidence *pythonpkg.ManifestEvidence) map[string]any {
	return map[string]any{
		"schema_version": "1.2", "run_id": runID.String(), "status": "completed", "writeback_enabled": false,
		"artifacts": map[string]any{artifact.TypeEvidenceProvenance: "evidence_provenance.jsonl"}, "evidence": evidence,
	}
}

func evidenceTestBlock() *pythonpkg.ManifestEvidence {
	return &pythonpkg.ManifestEvidence{
		Summary: pythonpkg.ManifestEvidenceSummary{Exact: 1, Ambiguous: 1, Unmatched: 1, Unavailable: 1},
		Fields: []pythonpkg.ManifestEvidenceField{{
			FieldID: "field_1", FieldKey: "power", QuestionText: "市电接入情况", AnswerValue: "两路市电", AnswerStatus: "partial_clue", TargetCell: "Sheet1!C4", SheetName: "Sheet1", RowIndex: 4,
			WritebackStatus: "uncertain", WritebackAction: "review_only", WritebackAllowed: false, Reasons: []string{"requires_review"},
			EvidenceRefs: []pythonpkg.ManifestEvidenceRef{
				{ChunkID: "chunk_1", DocumentID: "doc_1", TextPreview: "双路市电", Provenance: evidenceTestProvenance("exact", "前缀😀双路市电，验收合格", "双路市电", evidenceInt(3), evidenceInt(7))},
				{ChunkID: "chunk_2", Provenance: evidenceTestProvenance("ambiguous", "市电；市电", "市电", nil, nil)},
				{ChunkID: "chunk_3", Provenance: evidenceTestProvenance("unmatched", "暂无信息", "双路市电", nil, nil)},
				{Provenance: evidenceTestProvenance("unavailable", "", "", nil, nil)},
			},
		}},
	}
}

func evidenceTestProvenance(status, source, quote string, start, end *int) *pythonpkg.EvidenceProvenance {
	hash := ""
	if source != "" {
		sum := sha256.Sum256([]byte(source))
		hash = hex.EncodeToString(sum[:])
	}
	return &pythonpkg.EvidenceProvenance{MatchStatus: status, Reason: status + "_quote", Quote: quote, Start: start, End: end, SourceText: source, SourceTextHash: hash, TextSpace: "raw_source_text", IndexVersion: "v1"}
}

func evidenceInt(value int) *int { return &value }
