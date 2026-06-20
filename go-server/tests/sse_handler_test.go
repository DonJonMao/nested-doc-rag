package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/sse"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestSSEHandlerUsesRunAuthorizer(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	handler := sse.NewHandler(runevent.NewService(&fakeRunEventRepo{}, nil), sse.NewBroker(4), &fakeAuthorizer{})
	handler.SetRunAuthorizer(denyingRunAuthorizer{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	req := httptest.NewRequest(http.MethodGet, "/runs/"+runID.String()+"/events?workspace_id="+workspaceID.String(), nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusForbidden, rec.Code)
}

func TestSSEHandlerUsesLastEventIDHeaderWhenAfterSequenceMissing(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	reader := &recordingEventReader{err: httpx.NewAppError(httpx.CodeInternal, "stop stream", http.StatusInternalServerError, nil, nil)}
	handler := sse.NewHandler(reader, sse.NewBroker(4), &fakeAuthorizer{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	req := httptest.NewRequest(http.MethodGet, "/runs/"+runID.String()+"/events?workspace_id="+workspaceID.String(), nil)
	req.Header.Set("Last-Event-ID", "7")
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)

	require.Equal(t, int64(7), reader.afterSequence)
}

type denyingRunAuthorizer struct{}

func (denyingRunAuthorizer) CanReadRunEvents(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) error {
	return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
}

type recordingEventReader struct {
	afterSequence int64
	err           error
}

func (r *recordingEventReader) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]runevent.RunEvent, error) {
	r.afterSequence = afterSequence
	return nil, r.err
}
