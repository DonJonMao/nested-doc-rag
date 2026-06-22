package tests

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestFormHandlerUploadForm(t *testing.T) {
	workspaceID := uuid.New()
	forms := &fakeFormUseCase{form: &formpkg.FormFile{ID: uuid.New(), WorkspaceID: workspaceID, Filename: "form.xlsx"}}
	handler := formpkg.NewHandler(forms, &fakeFillRunUseCase{})
	body, contentType := multipartBody(t, map[string]string{"workspace_id": workspaceID.String()}, "file", "form.xlsx", []byte("content"))
	req := httptest.NewRequest(http.MethodPost, "/api/v1/forms", body)
	req.Header.Set("Content-Type", contentType)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	handler.UploadForm(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Len(t, forms.uploads, 1)
	require.Equal(t, workspaceID, forms.uploads[0].WorkspaceID)
}

func TestFormHandlerUploadFormMissingWorkspaceID(t *testing.T) {
	forms := &fakeFormUseCase{}
	handler := formpkg.NewHandler(forms, &fakeFillRunUseCase{})
	body, contentType := multipartBody(t, nil, "file", "form.xlsx", []byte("content"))
	req := httptest.NewRequest(http.MethodPost, "/api/v1/forms", body)
	req.Header.Set("Content-Type", contentType)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	handler.UploadForm(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	require.Contains(t, rec.Body.String(), httpx.CodeInvalidArgument)
	require.Empty(t, forms.uploads)
}

func TestFormHandlerUploadFormMissingFile(t *testing.T) {
	workspaceID := uuid.New()
	forms := &fakeFormUseCase{}
	handler := formpkg.NewHandler(forms, &fakeFillRunUseCase{})
	body, contentType := multipartBody(t, map[string]string{"workspace_id": workspaceID.String()}, "", "", nil)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/forms", body)
	req.Header.Set("Content-Type", contentType)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	handler.UploadForm(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	require.Contains(t, rec.Body.String(), httpx.CodeInvalidArgument)
	require.Empty(t, forms.uploads)
}

func TestFormHandlerListFormsRequiresWorkspaceID(t *testing.T) {
	handler := formpkg.NewHandler(&fakeFormUseCase{}, &fakeFillRunUseCase{})
	req := httptest.NewRequest(http.MethodGet, "/api/v1/forms", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	handler.ListForms(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	require.Contains(t, rec.Body.String(), httpx.CodeInvalidArgument)
}

func TestFormHandlerGetAndListForms(t *testing.T) {
	workspaceID := uuid.New()
	formID := uuid.New()
	forms := &fakeFormUseCase{
		form:  &formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, Filename: "form.xlsx"},
		forms: []formpkg.FormFile{{ID: formID, WorkspaceID: workspaceID, Filename: "form.xlsx"}},
	}
	handler := formpkg.NewHandler(forms, &fakeFillRunUseCase{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	actorCtx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})

	getRec := httptest.NewRecorder()
	router.ServeHTTP(getRec, httptest.NewRequest(http.MethodGet, "/forms/"+formID.String(), nil).WithContext(actorCtx))
	require.Equal(t, http.StatusOK, getRec.Code)

	listRec := httptest.NewRecorder()
	router.ServeHTTP(listRec, httptest.NewRequest(http.MethodGet, "/forms?workspace_id="+workspaceID.String(), nil).WithContext(actorCtx))
	require.Equal(t, http.StatusOK, listRec.Code)
}

func TestFillRunHandlerCreateGetListCancel(t *testing.T) {
	runID := uuid.New()
	workspaceID := uuid.New()
	runs := &fakeFillRunUseCase{run: &formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, Status: formpkg.FillRunStatusQueued}}
	handler := formpkg.NewHandler(&fakeFormUseCase{}, runs)
	actorCtx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})

	createReq := httptest.NewRequest(http.MethodPost, "/api/v1/fill-runs", strings.NewReader(`{"workspace_id":"`+workspaceID.String()+`","form_file_id":"`+uuid.New().String()+`","target_namespace":"target"}`)).WithContext(actorCtx)
	createRec := httptest.NewRecorder()
	handler.CreateFillRun(createRec, createReq)
	require.Equal(t, http.StatusOK, createRec.Code)
	require.Len(t, runs.created, 1)

	kbID := uuid.New()
	simpleReq := httptest.NewRequest(http.MethodPost, "/fill-runs/simple", strings.NewReader(`{"workspace_id":"`+workspaceID.String()+`","knowledge_base_id":"`+kbID.String()+`","form_file_id":"`+uuid.New().String()+`","name":"一号楼调研"}`)).WithContext(actorCtx)
	simpleRec := httptest.NewRecorder()
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	router.ServeHTTP(simpleRec, simpleReq)
	require.Equal(t, http.StatusOK, simpleRec.Code)
	require.Len(t, runs.created, 2)
	require.Equal(t, "一号楼调研", runs.created[1].Name)

	getReq := httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String(), nil).WithContext(actorCtx)
	getRec := httptest.NewRecorder()
	router.ServeHTTP(getRec, getReq)
	require.Equal(t, http.StatusOK, getRec.Code)

	listReq := httptest.NewRequest(http.MethodGet, "/fill-runs?workspace_id="+workspaceID.String(), nil).WithContext(actorCtx)
	listRec := httptest.NewRecorder()
	router.ServeHTTP(listRec, listReq)
	require.Equal(t, http.StatusOK, listRec.Code)

	cancelReq := httptest.NewRequest(http.MethodPost, "/fill-runs/"+runID.String()+"/cancel", nil).WithContext(actorCtx)
	cancelRec := httptest.NewRecorder()
	router.ServeHTTP(cancelRec, cancelReq)
	require.Equal(t, http.StatusOK, cancelRec.Code)
	require.Equal(t, []uuid.UUID{runID}, runs.canceled)
}

func TestFillRunHandlerCreateReturnsServiceError(t *testing.T) {
	workspaceID := uuid.New()
	runs := &fakeFillRunUseCase{err: httpx.NewAppError(httpx.CodeInvalidArgument, "target_namespace is required", http.StatusBadRequest, nil, nil)}
	handler := formpkg.NewHandler(&fakeFormUseCase{}, runs)
	req := httptest.NewRequest(http.MethodPost, "/api/v1/fill-runs", strings.NewReader(`{"workspace_id":"`+workspaceID.String()+`","form_file_id":"`+uuid.New().String()+`"}`))
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	handler.CreateFillRun(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	require.Contains(t, rec.Body.String(), "target_namespace is required")
	require.Len(t, runs.created, 1)
}

func TestFillRunHandlerDownloadShortcut(t *testing.T) {
	runID := uuid.New()
	runs := &fakeFillRunUseCase{download: &artifact.DownloadResult{Filename: "filled.xlsx", ContentType: "application/octet-stream", ContentLength: 6, Reader: io.NopCloser(strings.NewReader("result"))}}
	handler := formpkg.NewHandler(&fakeFormUseCase{}, runs)
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	req := httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String()+"/download/filled-form", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New()}))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "result", rec.Body.String())
	require.Equal(t, artifact.TypeFilledForm, runs.downloadTypes[0])
}

type fakeFormUseCase struct {
	form    *formpkg.FormFile
	forms   []formpkg.FormFile
	uploads []formpkg.UploadFormRequest
	err     error
}

func (f *fakeFormUseCase) UploadForm(ctx context.Context, req formpkg.UploadFormRequest, actor auth.Principal) (*formpkg.FormFile, error) {
	f.uploads = append(f.uploads, req)
	return f.form, f.err
}

func (f *fakeFormUseCase) GetForm(ctx context.Context, formID uuid.UUID, actor auth.Principal) (*formpkg.FormFile, error) {
	return f.form, f.err
}

func (f *fakeFormUseCase) ListForms(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]formpkg.FormFile, error) {
	return f.forms, f.err
}

type fakeFillRunUseCase struct {
	run           *formpkg.FillRun
	runs          []formpkg.FillRun
	created       []formpkg.CreateFillRunRequest
	canceled      []uuid.UUID
	download      *artifact.DownloadResult
	downloadTypes []string
	err           error
}

func (f *fakeFillRunUseCase) CreateFillRun(ctx context.Context, req formpkg.CreateFillRunRequest, actor auth.Principal) (*formpkg.FillRun, error) {
	f.created = append(f.created, req)
	return f.run, f.err
}

func (f *fakeFillRunUseCase) CreateSimpleFillRun(ctx context.Context, req formpkg.CreateSimpleFillRunRequest, actor auth.Principal) (*formpkg.FillRun, error) {
	f.created = append(f.created, formpkg.CreateFillRunRequest{WorkspaceID: req.WorkspaceID, FormFileID: req.FormFileID, Name: req.Name, KnowledgeBaseID: &req.KnowledgeBaseID, RoomContext: req.RoomContext})
	return f.run, f.err
}

func (f *fakeFillRunUseCase) GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*formpkg.FillRun, error) {
	return f.run, f.err
}

func (f *fakeFillRunUseCase) ListFillRuns(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]formpkg.FillRun, error) {
	return f.runs, f.err
}

func (f *fakeFillRunUseCase) GetFillRunDetail(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*formpkg.FillRunDetail, error) {
	if f.run == nil {
		return nil, f.err
	}
	return &formpkg.FillRunDetail{
		ID:                       f.run.ID,
		WorkspaceID:              f.run.WorkspaceID,
		Status:                   f.run.Status,
		ArtifactValidationStatus: formpkg.ArtifactValidationStatusValid,
		ManifestStatus:           formpkg.ManifestStatusValid,
		Summary:                  formpkg.FillRunSummaryCounts{TotalFields: f.run.ProgressTotal},
		Message:                  formpkg.SafeWritebackMessage,
	}, f.err
}

func (f *fakeFillRunUseCase) ListFillRunSummaries(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]formpkg.FillRunListItem, error) {
	out := make([]formpkg.FillRunListItem, 0, len(f.runs))
	for _, run := range f.runs {
		out = append(out, formpkg.FillRunListItem{ID: run.ID, WorkspaceID: run.WorkspaceID, Status: run.Status})
	}
	return out, f.err
}

func (f *fakeFillRunUseCase) CancelFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*formpkg.FillRun, error) {
	f.canceled = append(f.canceled, runID)
	return f.run, f.err
}

func (f *fakeFillRunUseCase) GetFillRunArtifacts(ctx context.Context, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	return nil, f.err
}

func (f *fakeFillRunUseCase) GetDownloadArtifactByType(ctx context.Context, runID uuid.UUID, artifactType string, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifactType)
	return f.download, f.err
}

func (f *fakeFillRunUseCase) DownloadFilledForm(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifact.TypeFilledForm)
	return f.download, f.err
}

func (f *fakeFillRunUseCase) DownloadReviewItems(ctx context.Context, runID uuid.UUID, format string, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifact.TypeReviewItems)
	return f.download, f.err
}

func (f *fakeFillRunUseCase) DownloadWritebackAudit(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifact.TypeWritebackAudit)
	return f.download, f.err
}

func (f *fakeFillRunUseCase) DownloadSummary(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifact.TypeSummary)
	return f.download, f.err
}

func (f *fakeFillRunUseCase) DownloadEvidenceImage(ctx context.Context, runID uuid.UUID, imageObjectKey string, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadTypes = append(f.downloadTypes, artifact.TypeImageEvidence)
	return f.download, f.err
}
