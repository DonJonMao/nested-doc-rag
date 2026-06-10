package knowledge

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type KnowledgeBaseUseCase interface {
	CreateKnowledgeBase(ctx context.Context, req CreateKnowledgeBaseRequest, actor auth.Principal) (*KnowledgeBase, error)
	GetKnowledgeBase(ctx context.Context, id uuid.UUID, actor auth.Principal) (*KnowledgeBase, error)
	ListKnowledgeBases(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]KnowledgeBase, error)
	ListIndexVersions(ctx context.Context, kbID uuid.UUID, limit int, offset int, actor auth.Principal) ([]KnowledgeIndexVersion, error)
	SetCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID, actor auth.Principal) (*KnowledgeBase, error)
}

type KnowledgeDocumentUseCase interface {
	UploadDocument(ctx context.Context, req UploadDocumentRequest, actor auth.Principal) (*KnowledgeDocument, error)
	ListDocuments(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]KnowledgeDocument, error)
	DeleteDocument(ctx context.Context, docID uuid.UUID, actor auth.Principal) error
}

type IngestionUseCase interface {
	CreateIngestionRun(ctx context.Context, req CreateIngestionRunRequest, actor auth.Principal) (*IngestionJob, error)
	GetIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*IngestionJob, error)
	ListIngestionJobs(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]IngestionJob, error)
	CancelIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*IngestionJob, error)
}

type Handler struct {
	bases      KnowledgeBaseUseCase
	documents  KnowledgeDocumentUseCase
	ingestions IngestionUseCase
}

func NewHandler(bases KnowledgeBaseUseCase, documents KnowledgeDocumentUseCase, ingestions IngestionUseCase) *Handler {
	return &Handler{bases: bases, documents: documents, ingestions: ingestions}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Post("/knowledge-bases", h.CreateKnowledgeBase)
	r.Get("/knowledge-bases", h.ListKnowledgeBases)
	r.Get("/knowledge-bases/{kb_id}", h.GetKnowledgeBase)
	r.Get("/knowledge-bases/{kb_id}/index-versions", h.ListIndexVersions)
	r.Post("/knowledge-bases/{kb_id}/current-index-version", h.SetCurrentIndexVersion)
	r.Post("/knowledge-bases/{kb_id}/documents", h.UploadDocument)
	r.Get("/knowledge-bases/{kb_id}/documents", h.ListDocuments)
	r.Delete("/documents/{doc_id}", h.DeleteDocument)
	r.Post("/knowledge-bases/{kb_id}/ingestion-runs", h.CreateIngestionRun)
	r.Get("/knowledge-bases/{kb_id}/ingestion-runs", h.ListIngestionJobs)
	r.Get("/ingestion-runs/{ingestion_job_id}", h.GetIngestionJob)
	r.Post("/ingestion-runs/{ingestion_job_id}/cancel", h.CancelIngestionJob)
	r.Get("/ingestion-runs/{ingestion_job_id}/events", h.IngestionEvents)
}

func (h *Handler) CreateKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req CreateKnowledgeBaseRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	kb, err := h.bases.CreateKnowledgeBase(r.Context(), req, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, kb)
}

func (h *Handler) ListKnowledgeBases(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := workspaceIDFromQuery(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	kbs, err := h.bases.ListKnowledgeBases(r.Context(), workspaceID, limitFromQuery(r), offsetFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, KnowledgeBaseListResponse{KnowledgeBases: kbs})
}

func (h *Handler) GetKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	kb, err := h.bases.GetKnowledgeBase(r.Context(), kbID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, kb)
}

func (h *Handler) ListIndexVersions(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	versions, err := h.bases.ListIndexVersions(r.Context(), kbID, limitFromQuery(r), offsetFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, IndexVersionListResponse{IndexVersions: versions})
}

func (h *Handler) SetCurrentIndexVersion(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	var req SetCurrentIndexVersionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	kb, err := h.bases.SetCurrentIndexVersion(r.Context(), kbID, req.IndexVersionID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, kb)
}

func (h *Handler) UploadDocument(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid multipart form", http.StatusBadRequest, nil, err))
		return
	}
	role := strings.TrimSpace(r.FormValue("document_role"))
	namespace := strings.TrimSpace(r.FormValue("namespace"))
	src, header, err := r.FormFile("file")
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "file is required", http.StatusBadRequest, nil, err))
		return
	}
	defer src.Close()
	doc, err := h.documents.UploadDocument(r.Context(), UploadDocumentRequest{
		KnowledgeBaseID:  kbID,
		OriginalFilename: header.Filename,
		Size:             header.Size,
		MIMEType:         header.Header.Get("Content-Type"),
		Reader:           src,
		DocumentRole:     role,
		Namespace:        namespace,
	}, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, doc)
}

func (h *Handler) ListDocuments(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	docs, err := h.documents.ListDocuments(r.Context(), kbID, strings.TrimSpace(r.URL.Query().Get("status")), limitFromQuery(r), offsetFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, KnowledgeDocumentListResponse{Documents: docs})
}

func (h *Handler) DeleteDocument(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	docID, err := uuid.Parse(chi.URLParam(r, "doc_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid document id", http.StatusBadRequest, nil, err))
		return
	}
	if err := h.documents.DeleteDocument(r.Context(), docID, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]bool{"deleted": true})
}

func (h *Handler) CreateIngestionRun(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	var req CreateIngestionRunRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	req.KnowledgeBaseID = kbID
	ingestion, err := h.ingestions.CreateIngestionRun(r.Context(), req, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, ingestion)
}

func (h *Handler) ListIngestionJobs(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	kbID, err := kbIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	jobs, err := h.ingestions.ListIngestionJobs(r.Context(), kbID, strings.TrimSpace(r.URL.Query().Get("status")), limitFromQuery(r), offsetFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, IngestionJobListResponse{IngestionJobs: jobs})
}

func (h *Handler) GetIngestionJob(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	ingestionID, err := ingestionIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	ingestion, err := h.ingestions.GetIngestionJob(r.Context(), ingestionID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, ingestion)
}

func (h *Handler) CancelIngestionJob(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	ingestionID, err := ingestionIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	ingestion, err := h.ingestions.CancelIngestionJob(r.Context(), ingestionID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, CancelIngestionJobResponse{IngestionJob: ingestion, Canceled: ingestion.Status == IngestionJobStatusCanceled})
}

func (h *Handler) IngestionEvents(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	ingestionID, err := ingestionIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	ingestion, err := h.ingestions.GetIngestionJob(r.Context(), ingestionID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	target := fmt.Sprintf("/api/v1/runs/%s/events?workspace_id=%s", ingestion.ID.String(), ingestion.WorkspaceID.String())
	http.Redirect(w, r, target, http.StatusTemporaryRedirect)
}

func workspaceIDFromQuery(r *http.Request) (uuid.UUID, error) {
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.URL.Query().Get("workspace_id")))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err)
	}
	return workspaceID, nil
}

func kbIDFromRequest(r *http.Request) (uuid.UUID, error) {
	kbID, err := uuid.Parse(chi.URLParam(r, "kb_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid knowledge base id", http.StatusBadRequest, nil, err)
	}
	return kbID, nil
}

func ingestionIDFromRequest(r *http.Request) (uuid.UUID, error) {
	ingestionID, err := uuid.Parse(chi.URLParam(r, "ingestion_job_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid ingestion job id", http.StatusBadRequest, nil, err)
	}
	return ingestionID, nil
}

func limitFromQuery(r *http.Request) int {
	limit := intFromQuery(r, "limit", 50)
	if limit > 200 {
		return 200
	}
	return limit
}

func offsetFromQuery(r *http.Request) int {
	return intFromQuery(r, "offset", 0)
}

func intFromQuery(r *http.Request, name string, fallback int) int {
	value := strings.TrimSpace(r.URL.Query().Get(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 0 {
		return fallback
	}
	return parsed
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
