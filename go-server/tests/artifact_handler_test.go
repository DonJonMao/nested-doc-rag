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
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestArtifactHandlerGetArtifact(t *testing.T) {
	artifactID := uuid.New()
	service := &fakeArtifactUseCase{artifact: &artifact.RunArtifact{ID: artifactID, Filename: "run_manifest.json"}}
	router := artifactHandlerRouter(artifact.NewHandler(service))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/artifacts/"+artifactID.String(), nil)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Contains(t, rec.Body.String(), artifactID.String())
}

func TestArtifactHandlerDownloadArtifact(t *testing.T) {
	artifactID := uuid.New()
	service := &fakeArtifactUseCase{download: &artifact.DownloadResult{Filename: "run_manifest.json", ContentType: "application/json", ContentLength: 2, Reader: io.NopCloser(strings.NewReader("{}"))}}
	router := artifactHandlerRouter(artifact.NewHandler(service))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/artifacts/"+artifactID.String()+"/download", nil)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "{}", rec.Body.String())
	require.Contains(t, rec.Header().Get("Content-Disposition"), "attachment")
}

func TestArtifactHandlerListRunArtifacts(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	service := &fakeArtifactUseCase{artifacts: []artifact.RunArtifact{{ID: uuid.New(), WorkspaceID: workspaceID, RunID: runID}}}
	router := artifactHandlerRouter(artifact.NewHandler(service))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/runs/"+runID.String()+"/artifacts?workspace_id="+workspaceID.String(), nil)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Contains(t, rec.Body.String(), runID.String())
}

type fakeArtifactUseCase struct {
	artifact  *artifact.RunArtifact
	artifacts []artifact.RunArtifact
	download  *artifact.DownloadResult
}

func (f *fakeArtifactUseCase) GetArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.RunArtifact, error) {
	return f.artifact, nil
}

func (f *fakeArtifactUseCase) ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	return f.artifacts, nil
}

func (f *fakeArtifactUseCase) DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	return f.download, nil
}

func artifactHandlerRouter(handler *artifact.Handler) http.Handler {
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					principal := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
					next.ServeHTTP(w, r.WithContext(auth.ContextWithPrincipal(r.Context(), principal)))
				})
			})
			handler.RegisterRoutes(protected)
		})
	})
	return router
}
