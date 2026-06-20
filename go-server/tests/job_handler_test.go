package tests

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/sse"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestJobHandlerListGetCancel(t *testing.T) {
	repo := newFakeJobRepo()
	queue := &fakeQueue{}
	service := jobs.NewService(repo, nil, queue, &fakeAuthorizer{}, nil, nil, 3)
	handler := jobs.NewHandler(service, true)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	workspaceID := uuid.New()
	job := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 3, CreatedBy: actor.UserID}
	repo.add(job)
	router := authenticatedJobRouter(handler, actor, true)

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/jobs?workspace_id="+workspaceID.String(), nil))
	require.Equal(t, http.StatusOK, rec.Code)

	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/jobs/"+job.ID.String(), nil))
	require.Equal(t, http.StatusOK, rec.Code)

	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/jobs/"+job.ID.String()+"/cancel", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	updated, err := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusCanceled, updated.Status)
}

func TestJobHandlerNoopAdminOnlyAndDisabled(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	body, _ := json.Marshal(jobs.NoopJobRequest{WorkspaceID: workspaceID, SleepMS: 1})
	disabled := jobs.NewHandler(jobs.NewService(newFakeJobRepo(), nil, &fakeQueue{}, &fakeAuthorizer{}, nil, nil, 3), false)
	router := authenticatedJobRouter(disabled, actor, true)

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/admin/noop-jobs", bytes.NewReader(body)))
	require.Equal(t, http.StatusNotFound, rec.Code)

	enabledRepo := newFakeJobRepo()
	enabledQueue := &fakeQueue{}
	enabled := jobs.NewHandler(jobs.NewService(enabledRepo, nil, enabledQueue, &fakeAuthorizer{}, nil, nil, 3), true)
	router = authenticatedJobRouter(enabled, actor, true)
	rec = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/admin/noop-jobs", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Len(t, enabledQueue.enqueued, 1)
}

func TestJobHandlerNoopRequiresAdminRoute(t *testing.T) {
	workspaceID := uuid.New()
	body, _ := json.Marshal(jobs.NoopJobRequest{WorkspaceID: workspaceID})
	handler := jobs.NewHandler(jobs.NewService(newFakeJobRepo(), nil, &fakeQueue{}, &fakeAuthorizer{}, nil, nil, 3), true)
	router := authenticatedJobRouter(handler, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleViewer}}, true)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/admin/noop-jobs", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusForbidden, rec.Code)
	require.Contains(t, rec.Body.String(), httpx.CodeForbidden)
}

func TestBlock3RoutesRequireAuthAndExist(t *testing.T) {
	manager, err := auth.NewTokenManager("block3-route-secret", time.Minute)
	require.NoError(t, err)
	handler := jobs.NewHandler(jobs.NewService(newFakeJobRepo(), nil, &fakeQueue{}, &fakeAuthorizer{}, nil, nil, 3), false)
	router := chi.NewRouter()
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(manager))
			handler.RegisterRoutes(protected)
			sse.NewHandler(runevent.NewService(&fakeRunEventRepo{}, nil), sse.NewBroker(4), &fakeAuthorizer{}).RegisterRoutes(protected)
		})
	})
	paths := []string{
		"/api/v1/jobs?workspace_id=" + uuid.NewString(),
		"/api/v1/jobs/" + uuid.NewString(),
		"/api/v1/jobs/" + uuid.NewString() + "/cancel",
		"/api/v1/runs/" + uuid.NewString() + "/events?workspace_id=" + uuid.NewString(),
	}
	for _, path := range paths {
		method := http.MethodGet
		if path[len(path)-6:] == "cancel" {
			method = http.MethodPost
		}
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(method, path, nil))
		require.Equal(t, http.StatusUnauthorized, rec.Code)
	}
	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "job-user"}, []string{auth.RoleOperator})
	require.NoError(t, err)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/jobs?workspace_id="+uuid.NewString(), nil)
	req.Header.Set("Authorization", "Bearer "+token)
	router.ServeHTTP(rec, req)
	require.NotEqual(t, http.StatusNotFound, rec.Code)

	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/api/v1/runs/"+uuid.NewString()+"/events", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	router.ServeHTTP(rec, req)
	require.Equal(t, http.StatusBadRequest, rec.Code)
}

func authenticatedJobRouter(handler *jobs.Handler, actor auth.Principal, includeAdmin bool) http.Handler {
	router := chi.NewRouter()
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					next.ServeHTTP(w, r.WithContext(auth.ContextWithPrincipal(r.Context(), actor)))
				})
			})
			handler.RegisterRoutes(protected)
			if includeAdmin {
				protected.Group(func(admin chi.Router) {
					admin.Use(middleware.RequireRoles(auth.RoleAdmin))
					handler.RegisterAdminRoutes(admin)
				})
			}
		})
	})
	return router
}
