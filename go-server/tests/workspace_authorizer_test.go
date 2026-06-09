package tests

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/workspace"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestWorkspaceAuthorizerAdminReadWrite(t *testing.T) {
	authorizer := workspace.NewAuthorizer(newFakeWorkspaceRepo())
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}

	require.NoError(t, authorizer.CanReadWorkspace(context.Background(), uuid.New(), actor))
	require.NoError(t, authorizer.CanWriteWorkspace(context.Background(), uuid.New(), actor))
}

func TestWorkspaceAuthorizerMemberRead(t *testing.T) {
	repo := newFakeWorkspaceRepo()
	workspaceID := uuid.New()
	userID := uuid.New()
	require.NoError(t, repo.AddMember(context.Background(), workspaceID, userID, workspace.RoleViewer))
	authorizer := workspace.NewAuthorizer(repo)

	err := authorizer.CanReadWorkspace(context.Background(), workspaceID, auth.Principal{UserID: userID, Roles: []string{auth.RoleViewer}})

	require.NoError(t, err)
}

func TestWorkspaceAuthorizerNonMemberReadForbidden(t *testing.T) {
	authorizer := workspace.NewAuthorizer(newFakeWorkspaceRepo())

	err := authorizer.CanReadWorkspace(context.Background(), uuid.New(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleViewer}})

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
}

func TestWorkspaceAuthorizerWriteRoles(t *testing.T) {
	for _, role := range []string{workspace.RoleOwner, workspace.RoleOperator} {
		repo := newFakeWorkspaceRepo()
		workspaceID := uuid.New()
		userID := uuid.New()
		require.NoError(t, repo.AddMember(context.Background(), workspaceID, userID, role))
		authorizer := workspace.NewAuthorizer(repo)

		err := authorizer.CanWriteWorkspace(context.Background(), workspaceID, auth.Principal{UserID: userID, Roles: []string{auth.RoleOperator}})

		require.NoError(t, err)
	}
}

func TestWorkspaceAuthorizerReviewerViewerWriteForbidden(t *testing.T) {
	for _, role := range []string{workspace.RoleReviewer, workspace.RoleViewer} {
		repo := newFakeWorkspaceRepo()
		workspaceID := uuid.New()
		userID := uuid.New()
		require.NoError(t, repo.AddMember(context.Background(), workspaceID, userID, role))
		authorizer := workspace.NewAuthorizer(repo)

		err := authorizer.CanWriteWorkspace(context.Background(), workspaceID, auth.Principal{UserID: userID, Roles: []string{auth.RoleOperator}})

		requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	}
}
