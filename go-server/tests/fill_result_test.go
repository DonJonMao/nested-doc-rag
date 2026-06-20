package tests

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunDetailReadsManifestAndSummary(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{
		artifact.TypeSummary:        "summary.json",
		artifact.TypeFilledForm:     "filled_form.xlsx",
		artifact.TypeReviewItems:    "review_items.jsonl",
		artifact.TypeWritebackAudit: "writeback_audit.jsonl",
	})
	fixture.addJSONArtifact(artifact.TypeSummary, "summary.json", map[string]any{
		"field_count": 141,
		"raw_status_counts": map[string]any{
			"answered":            50,
			"partial_clue":        72,
			"not_found":           18,
			"conflict_unresolved": 1,
		},
		"overlay_counts": map[string]any{
			"writeback_allowed": 49,
			"review_required":   92,
		},
		"trace_summary": map[string]any{"failed_count": 0},
	})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "xlsx")
	fixture.addArtifact(artifact.TypeReviewItems, "review_items.jsonl", "")
	fixture.addArtifact(artifact.TypeWritebackAudit, "writeback_audit.jsonl", "")

	detail, err := fixture.service.GetFillRunDetail(context.Background(), fixture.run.ID, fixture.actor)

	require.NoError(t, err)
	require.Equal(t, "completed", detail.Status)
	require.Equal(t, formpkg.ManifestStatusValid, detail.ManifestStatus)
	require.Equal(t, formpkg.ArtifactValidationStatusValid, detail.ArtifactValidationStatus)
	require.Equal(t, 141, detail.Summary.TotalFields)
	require.Equal(t, 50, detail.Summary.Answered)
	require.Equal(t, 49, detail.Summary.WritebackAllowed)
	require.Equal(t, 92, detail.Summary.ReviewRequired)
	require.True(t, detail.Artifacts.FilledForm.Available)
	require.Equal(t, formpkg.SafeWritebackMessage, detail.Message)
}

func TestDownloadFilledFormSuccess(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{
		artifact.TypeSummary:    "summary.json",
		artifact.TypeFilledForm: "filled_form.xlsx",
	})
	fixture.addJSONArtifact(artifact.TypeSummary, "summary.json", map[string]any{"total_fields": 1})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "filled")
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), fixture.actor))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "filled", rec.Body.String())
	require.Equal(t, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", rec.Header().Get("Content-Type"))
	require.Contains(t, rec.Header().Get("Content-Disposition"), `attachment; filename="filled_form.xlsx"`)
}

func TestDownloadFilledFormNotReady(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusRunning)
	fixture.addManifest(map[string]any{artifact.TypeFilledForm: "filled_form.xlsx"})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "filled")
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), fixture.actor))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusConflict, rec.Code)
	require.Contains(t, rec.Body.String(), "not completed yet")
}

func TestDownloadFilledFormMissingArtifact(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{artifact.TypeFilledForm: "filled_form.xlsx"})
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), fixture.actor))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusNotFound, rec.Code)
}

func TestDownloadRejectsPathTraversal(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{artifact.TypeFilledForm: "../secret.xlsx"})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "filled")
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), fixture.actor))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusConflict, rec.Code)
	require.Contains(t, rec.Body.String(), "manifest")
}

func TestReviewItemsCSVExport(t *testing.T) {
	var buf bytes.Buffer
	err := formpkg.ReviewItemsJSONLToCSV(strings.NewReader(`{"field_id":"item_1","row_index":4,"question_text":"市电","answer_status":"partial_clue","answer_value":"2路","risk_level":"medium","review_required":true,"writeback_allowed":false,"reasons":["weak_evidence","needs_review"],"source_chunk_ids":["c1","c2"],"notes":"人工确认"}
bad-json
{"field_id":"item_2","candidate_chunk_ids":["c3"],"reason":"not_found","proposed_answer":"未找到"}`), &buf)

	require.NoError(t, err)
	csv := buf.String()
	require.Contains(t, csv, "field_id,row_index,question_text,answer_status,answer_value,risk_level,review_required,writeback_allowed,reasons,source_chunk_ids,notes")
	require.Contains(t, csv, "item_1,4,市电,partial_clue,2路,medium,true,false,weak_evidence;needs_review,c1;c2,人工确认")
	require.Contains(t, csv, "item_2,,,,未找到,,,,not_found,c3,")
}

func TestUserCannotDownloadOtherUsersFilledForm(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{artifact.TypeFilledForm: "filled_form.xlsx"})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "filled")
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusNotFound, rec.Code)
}

func TestArtifactValidationBlocksInvalidDownload(t *testing.T) {
	fixture := newFillResultFixture(t, formpkg.FillRunStatusSucceeded)
	fixture.addManifest(map[string]any{
		artifact.TypeFilledForm:     "filled_form.xlsx",
		artifact.TypeWritebackAudit: "writeback_audit.jsonl",
	})
	fixture.addArtifact(artifact.TypeFilledForm, "filled_form.xlsx", "filled")
	router := fixture.router()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+fixture.run.ID.String()+"/downloads/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), fixture.actor))
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusConflict, rec.Code)
	require.Contains(t, rec.Body.String(), "artifact validation failed")
}

type fillResultFixture struct {
	actor      auth.Principal
	workspace  uuid.UUID
	run        formpkg.FillRun
	forms      *fakeFormFileRepo
	runs       *fakeFillRunRepo
	artifacts  *resultArtifactService
	authorizer *fakeAuthorizer
	service    *formpkg.FillRunService
}

func newFillResultFixture(t *testing.T, status string) *fillResultFixture {
	t.Helper()
	workspaceID := uuid.New()
	formID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	now := time.Now().UTC()
	finishedAt := now
	if status == formpkg.FillRunStatusRunning || status == formpkg.FillRunStatusQueued || status == formpkg.FillRunStatusCreated {
		finishedAt = time.Time{}
	}
	run := formpkg.FillRun{
		ID:                  uuid.New(),
		WorkspaceID:         workspaceID,
		FormFileID:          formID,
		Name:                "西咸4号楼填表",
		Status:              status,
		ProgressTotal:       1,
		CreatedBy:           actor.UserID,
		CreatedAt:           now,
		UpdatedAt:           now,
		AnsweredCount:       1,
		ReviewRequiredCount: 0,
	}
	if !finishedAt.IsZero() {
		run.FinishedAt = &finishedAt
	}
	forms := newFakeFormFileRepo()
	require.NoError(t, forms.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, Filename: "template.xlsx", CreatedBy: actor.UserID, CreatedAt: now}))
	runs := newFakeFillRunRepo()
	require.NoError(t, runs.Create(context.Background(), run))
	artifactSvc := newResultArtifactService(workspaceID, run.ID)
	authorizer := &fakeAuthorizer{}
	service := formpkg.NewFillRunService(runs, forms, &fakeJobUseCase{}, artifactSvc, authorizer, nil, zap.NewNop(), *config.Default())
	return &fillResultFixture{actor: actor, workspace: workspaceID, run: run, forms: forms, runs: runs, artifacts: artifactSvc, authorizer: authorizer, service: service}
}

func (f *fillResultFixture) router() http.Handler {
	router := chi.NewRouter()
	formpkg.NewHandler(&fakeFormUseCase{}, f.service).RegisterRoutes(router)
	return router
}

func (f *fillResultFixture) addManifest(artifacts map[string]any) {
	data, _ := json.Marshal(map[string]any{
		"run_id":            f.run.ID.String(),
		"status":            "completed",
		"writeback_enabled": true,
		"artifacts":         artifacts,
		"counts":            map[string]any{"total_fields": 1, "answered": 1, "writeback_allowed": 1},
	})
	f.addArtifact(artifact.TypeRunManifest, "run_manifest.json", string(data))
}

func (f *fillResultFixture) addJSONArtifact(artifactType string, filename string, payload map[string]any) {
	data, _ := json.Marshal(payload)
	f.addArtifact(artifactType, filename, string(data))
}

func (f *fillResultFixture) addArtifact(artifactType string, filename string, content string) {
	f.artifacts.add(artifactType, filename, content)
}

type resultArtifactService struct {
	workspaceID uuid.UUID
	runID       uuid.UUID
	artifacts   []artifact.RunArtifact
	content     map[uuid.UUID]string
}

func newResultArtifactService(workspaceID uuid.UUID, runID uuid.UUID) *resultArtifactService {
	return &resultArtifactService{workspaceID: workspaceID, runID: runID, content: make(map[uuid.UUID]string)}
}

func (s *resultArtifactService) add(artifactType string, filename string, content string) {
	id := uuid.New()
	contentType := "application/octet-stream"
	if strings.HasSuffix(filename, ".json") {
		contentType = "application/json"
	} else if strings.HasSuffix(filename, ".jsonl") {
		contentType = "application/x-ndjson"
	}
	s.artifacts = append(s.artifacts, artifact.RunArtifact{
		ID:           id,
		WorkspaceID:  s.workspaceID,
		RunID:        s.runID,
		ArtifactType: artifactType,
		Filename:     filename,
		ObjectKey:    "test/" + id.String(),
		ContentType:  contentType,
		FileSize:     int64(len(content)),
		CreatedBy:    uuid.New(),
		CreatedAt:    time.Now().UTC(),
	})
	s.content[id] = content
}

func (s *resultArtifactService) ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	if workspaceID != s.workspaceID || runID != s.runID {
		return nil, nil
	}
	return append([]artifact.RunArtifact(nil), s.artifacts...), nil
}

func (s *resultArtifactService) DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	return s.open(artifactID)
}

func (s *resultArtifactService) DownloadArtifactProxy(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	return s.open(artifactID)
}

func (s *resultArtifactService) OpenArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	return s.open(artifactID)
}

func (s *resultArtifactService) open(artifactID uuid.UUID) (*artifact.DownloadResult, error) {
	for _, item := range s.artifacts {
		if item.ID == artifactID {
			content := s.content[artifactID]
			return &artifact.DownloadResult{
				Filename:      item.Filename,
				ContentType:   item.ContentType,
				ContentLength: int64(len(content)),
				Reader:        io.NopCloser(strings.NewReader(content)),
			}, nil
		}
	}
	return nil, httpx.NewAppError(httpx.CodeNotFound, "artifact not found", http.StatusNotFound, nil, nil)
}
