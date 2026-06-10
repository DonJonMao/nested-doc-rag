package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBlock6RoutesRequireAuth(t *testing.T) {
	router := block6RouteWiringRouter(t)
	kbID := uuid.NewString()
	ingestionID := uuid.NewString()
	docID := uuid.NewString()
	cases := []struct {
		method string
		path   string
	}{
		{http.MethodPost, "/api/v1/knowledge-bases"},
		{http.MethodGet, "/api/v1/knowledge-bases?workspace_id=" + uuid.NewString()},
		{http.MethodGet, "/api/v1/knowledge-bases/" + kbID},
		{http.MethodGet, "/api/v1/knowledge-bases/" + kbID + "/index-versions"},
		{http.MethodPost, "/api/v1/knowledge-bases/" + kbID + "/current-index-version"},
		{http.MethodPost, "/api/v1/knowledge-bases/" + kbID + "/documents"},
		{http.MethodGet, "/api/v1/knowledge-bases/" + kbID + "/documents"},
		{http.MethodDelete, "/api/v1/documents/" + docID},
		{http.MethodPost, "/api/v1/knowledge-bases/" + kbID + "/ingestion-runs"},
		{http.MethodGet, "/api/v1/knowledge-bases/" + kbID + "/ingestion-runs"},
		{http.MethodGet, "/api/v1/ingestion-runs/" + ingestionID},
		{http.MethodPost, "/api/v1/ingestion-runs/" + ingestionID + "/cancel"},
		{http.MethodGet, "/api/v1/ingestion-runs/" + ingestionID + "/events"},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, nil))

		require.Equal(t, http.StatusUnauthorized, rec.Code, tc.path)
		require.Contains(t, rec.Body.String(), httpx.CodeUnauthorized, tc.path)
	}
}

func TestBlock6RoutesExistWithAuth(t *testing.T) {
	router := block6RouteWiringRouter(t)
	token := block6RouteToken(t)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/knowledge-bases?workspace_id="+uuid.NewString(), nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)

	require.NotEqual(t, http.StatusNotFound, rec.Code)
}

func block6RouteWiringRouter(t *testing.T) http.Handler {
	t.Helper()
	manager, err := auth.NewTokenManager("block6-route-secret", time.Minute)
	require.NoError(t, err)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(manager))
			knowledgepkg.NewHandler(&fakeKnowledgeBaseUseCase{}, &fakeKnowledgeDocumentUseCase{}, &fakeIngestionUseCase{}).RegisterRoutes(protected)
		})
	})
	return router
}

func block6RouteToken(t *testing.T) string {
	t.Helper()
	manager, err := auth.NewTokenManager("block6-route-secret", time.Minute)
	require.NoError(t, err)
	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "route-user"}, []string{auth.RoleOperator})
	require.NoError(t, err)
	return token
}
