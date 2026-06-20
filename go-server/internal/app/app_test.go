package app

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestRunEventAccessAuthorizerAllowsOwnedFillRun(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	authorizer := runEventAccessAuthorizer{
		workspaceAuthorizer: &recordingWorkspaceAuthorizer{},
		fillRuns: &stubFillRunReader{run: &formpkg.FillRun{
			ID:          runID,
			WorkspaceID: workspaceID,
			CreatedBy:   actor.UserID,
		}},
	}

	err := authorizer.CanReadRunEvents(context.Background(), workspaceID, runID, actor)

	require.NoError(t, err)
}

func TestRunEventAccessAuthorizerDoesNotFallbackToWorkspaceForUnknownRun(t *testing.T) {
	workspaceID := uuid.New()
	workspaceAuth := &recordingWorkspaceAuthorizer{}
	authorizer := runEventAccessAuthorizer{
		workspaceAuthorizer: workspaceAuth,
		fillRuns:            &stubFillRunReader{err: notFoundAppError("fill run not found")},
		ingestions:          &stubIngestionReader{err: notFoundAppError("ingestion job not found")},
	}

	err := authorizer.CanReadRunArtifact(context.Background(), workspaceID, uuid.New(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})

	requireAppHTTPError(t, err, httpx.CodeNotFound, http.StatusNotFound)
	require.Equal(t, 0, workspaceAuth.reads)
}

func TestRunEventAccessAuthorizerRequiresAdminForIngestionEvents(t *testing.T) {
	workspaceID := uuid.New()
	ingestionID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	ingestions := &stubIngestionReader{job: &knowledgepkg.IngestionJob{ID: ingestionID, WorkspaceID: workspaceID}}
	authorizer := runEventAccessAuthorizer{
		workspaceAuthorizer: &recordingWorkspaceAuthorizer{},
		fillRuns:            &stubFillRunReader{err: notFoundAppError("fill run not found")},
		ingestions:          ingestions,
	}

	err := authorizer.CanReadRunEvents(context.Background(), workspaceID, ingestionID, actor)

	requireAppHTTPError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	require.Contains(t, ingestions.rolesSeen, auth.RoleAdmin)
}

type recordingWorkspaceAuthorizer struct {
	reads int
	err   error
}

func (a *recordingWorkspaceAuthorizer) CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	a.reads++
	return a.err
}

type stubFillRunReader struct {
	run *formpkg.FillRun
	err error
}

func (r *stubFillRunReader) GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*formpkg.FillRun, error) {
	if r.err != nil {
		return nil, r.err
	}
	return r.run, nil
}

type stubIngestionReader struct {
	job       *knowledgepkg.IngestionJob
	err       error
	rolesSeen []string
}

func (r *stubIngestionReader) GetIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*knowledgepkg.IngestionJob, error) {
	r.rolesSeen = append([]string(nil), actor.Roles...)
	if r.err != nil {
		return nil, r.err
	}
	return r.job, nil
}

func notFoundAppError(message string) error {
	return httpx.NewAppError(httpx.CodeNotFound, message, http.StatusNotFound, nil, nil)
}

func requireAppHTTPError(t *testing.T, err error, code string, status int) {
	t.Helper()
	require.Error(t, err)
	appErr := httpx.ErrorFrom(err)
	require.Equal(t, code, appErr.Code)
	require.Equal(t, status, appErr.HTTPStatus)
}
