package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestKnowledgeHandlerCreateListGetKnowledgeBase(t *testing.T) {
	workspaceID := uuid.New()
	kbID := uuid.New()
	bases := &fakeKnowledgeBaseUseCase{kb: &knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}, kbs: []knowledgepkg.KnowledgeBase{{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}}}
	handler := knowledgepkg.NewHandler(bases, &fakeKnowledgeDocumentUseCase{}, &fakeIngestionUseCase{})
	router := authenticatedKnowledgeRouter(handler)

	createRec := httptest.NewRecorder()
	router.ServeHTTP(createRec, httptest.NewRequest(http.MethodPost, "/knowledge-bases", strings.NewReader(`{"workspace_id":"`+workspaceID.String()+`","name":"kb"}`)))
	require.Equal(t, http.StatusOK, createRec.Code)
	require.Len(t, bases.created, 1)

	listRec := httptest.NewRecorder()
	router.ServeHTTP(listRec, httptest.NewRequest(http.MethodGet, "/knowledge-bases?workspace_id="+workspaceID.String(), nil))
	require.Equal(t, http.StatusOK, listRec.Code)

	getRec := httptest.NewRecorder()
	router.ServeHTTP(getRec, httptest.NewRequest(http.MethodGet, "/knowledge-bases/"+kbID.String(), nil))
	require.Equal(t, http.StatusOK, getRec.Code)
}

func TestKnowledgeHandlerUploadAndDeleteDocument(t *testing.T) {
	kbID := uuid.New()
	docID := uuid.New()
	docs := &fakeKnowledgeDocumentUseCase{doc: &knowledgepkg.KnowledgeDocument{ID: docID, KnowledgeBaseID: kbID, Filename: "doc.xlsx"}}
	handler := knowledgepkg.NewHandler(&fakeKnowledgeBaseUseCase{}, docs, &fakeIngestionUseCase{})
	router := authenticatedKnowledgeRouter(handler)
	body, contentType := multipartBody(t, map[string]string{"document_role": knowledgepkg.DocumentRoleKnowledgeBase, "namespace": "xixian_4"}, "file", "doc.xlsx", []byte("content"))

	uploadReq := httptest.NewRequest(http.MethodPost, "/knowledge-bases/"+kbID.String()+"/documents", body)
	uploadReq.Header.Set("Content-Type", contentType)
	uploadRec := httptest.NewRecorder()
	router.ServeHTTP(uploadRec, uploadReq)
	require.Equal(t, http.StatusOK, uploadRec.Code)
	require.Len(t, docs.uploaded, 1)
	require.Equal(t, knowledgepkg.DocumentRoleKnowledgeBase, docs.uploaded[0].DocumentRole)

	deleteRec := httptest.NewRecorder()
	router.ServeHTTP(deleteRec, httptest.NewRequest(http.MethodDelete, "/documents/"+docID.String(), nil))
	require.Equal(t, http.StatusOK, deleteRec.Code)
	require.Equal(t, []uuid.UUID{docID}, docs.deleted)
}

func TestKnowledgeHandlerCreateGetCancelIngestion(t *testing.T) {
	kbID := uuid.New()
	ingestionID := uuid.New()
	ingestions := &fakeIngestionUseCase{job: &knowledgepkg.IngestionJob{ID: ingestionID, KnowledgeBaseID: kbID, Status: knowledgepkg.IngestionJobStatusQueued}}
	handler := knowledgepkg.NewHandler(&fakeKnowledgeBaseUseCase{}, &fakeKnowledgeDocumentUseCase{}, ingestions)
	router := authenticatedKnowledgeRouter(handler)

	createRec := httptest.NewRecorder()
	router.ServeHTTP(createRec, httptest.NewRequest(http.MethodPost, "/knowledge-bases/"+kbID.String()+"/ingestion-runs", strings.NewReader(`{"namespace":"xixian_4","resume":true}`)))
	require.Equal(t, http.StatusOK, createRec.Code)
	require.Len(t, ingestions.created, 1)
	require.Equal(t, kbID, ingestions.created[0].KnowledgeBaseID)

	getRec := httptest.NewRecorder()
	router.ServeHTTP(getRec, httptest.NewRequest(http.MethodGet, "/ingestion-runs/"+ingestionID.String(), nil))
	require.Equal(t, http.StatusOK, getRec.Code)

	cancelRec := httptest.NewRecorder()
	router.ServeHTTP(cancelRec, httptest.NewRequest(http.MethodPost, "/ingestion-runs/"+ingestionID.String()+"/cancel", nil))
	require.Equal(t, http.StatusOK, cancelRec.Code)
	require.Equal(t, []uuid.UUID{ingestionID}, ingestions.canceled)
}

func authenticatedKnowledgeRouter(handler *knowledgepkg.Handler) http.Handler {
	router := chi.NewRouter()
	router.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			next.ServeHTTP(w, r.WithContext(auth.ContextWithPrincipal(r.Context(), auth.Principal{UserID: uuid.New()})))
		})
	})
	handler.RegisterRoutes(router)
	return router
}

type fakeKnowledgeBaseUseCase struct {
	kb      *knowledgepkg.KnowledgeBase
	kbs     []knowledgepkg.KnowledgeBase
	created []knowledgepkg.CreateKnowledgeBaseRequest
}

func (f *fakeKnowledgeBaseUseCase) CreateKnowledgeBase(ctx context.Context, req knowledgepkg.CreateKnowledgeBaseRequest, actor auth.Principal) (*knowledgepkg.KnowledgeBase, error) {
	f.created = append(f.created, req)
	return f.kb, nil
}

func (f *fakeKnowledgeBaseUseCase) GetKnowledgeBase(ctx context.Context, id uuid.UUID, actor auth.Principal) (*knowledgepkg.KnowledgeBase, error) {
	return f.kb, nil
}

func (f *fakeKnowledgeBaseUseCase) ListKnowledgeBases(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]knowledgepkg.KnowledgeBase, error) {
	return f.kbs, nil
}

func (f *fakeKnowledgeBaseUseCase) ListIndexVersions(ctx context.Context, kbID uuid.UUID, limit int, offset int, actor auth.Principal) ([]knowledgepkg.KnowledgeIndexVersion, error) {
	return nil, nil
}

func (f *fakeKnowledgeBaseUseCase) SetCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID, actor auth.Principal) (*knowledgepkg.KnowledgeBase, error) {
	return f.kb, nil
}

type fakeKnowledgeDocumentUseCase struct {
	doc      *knowledgepkg.KnowledgeDocument
	docs     []knowledgepkg.KnowledgeDocument
	uploaded []knowledgepkg.UploadDocumentRequest
	deleted  []uuid.UUID
}

func (f *fakeKnowledgeDocumentUseCase) UploadDocument(ctx context.Context, req knowledgepkg.UploadDocumentRequest, actor auth.Principal) (*knowledgepkg.KnowledgeDocument, error) {
	f.uploaded = append(f.uploaded, req)
	return f.doc, nil
}

func (f *fakeKnowledgeDocumentUseCase) ListDocuments(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]knowledgepkg.KnowledgeDocument, error) {
	return f.docs, nil
}

func (f *fakeKnowledgeDocumentUseCase) DeleteDocument(ctx context.Context, docID uuid.UUID, actor auth.Principal) error {
	f.deleted = append(f.deleted, docID)
	return nil
}

type fakeIngestionUseCase struct {
	job      *knowledgepkg.IngestionJob
	jobs     []knowledgepkg.IngestionJob
	created  []knowledgepkg.CreateIngestionRunRequest
	canceled []uuid.UUID
}

func (f *fakeIngestionUseCase) CreateIngestionRun(ctx context.Context, req knowledgepkg.CreateIngestionRunRequest, actor auth.Principal) (*knowledgepkg.IngestionJob, error) {
	f.created = append(f.created, req)
	return f.job, nil
}

func (f *fakeIngestionUseCase) GetIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*knowledgepkg.IngestionJob, error) {
	return f.job, nil
}

func (f *fakeIngestionUseCase) ListIngestionJobs(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]knowledgepkg.IngestionJob, error) {
	return f.jobs, nil
}

func (f *fakeIngestionUseCase) CancelIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*knowledgepkg.IngestionJob, error) {
	f.canceled = append(f.canceled, id)
	return f.job, nil
}
