package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/workspace"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestWorkspaceServiceCreateWorkspaceCreatesOwnerMembership(t *testing.T) {
	repo := newFakeWorkspaceRepo()
	service := workspace.NewService(repo, nil)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}

	ws, err := service.CreateWorkspace(context.Background(), workspace.CreateWorkspaceRequest{Name: "西咸数据中心"}, actor)

	require.NoError(t, err)
	role, err := repo.GetMemberRole(context.Background(), ws.ID, actor.UserID)
	require.NoError(t, err)
	require.Equal(t, workspace.RoleOwner, role)
}

func TestWorkspaceServiceListMyWorkspaces(t *testing.T) {
	repo := newFakeWorkspaceRepo()
	service := workspace.NewService(repo, nil)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	_, err := service.CreateWorkspace(context.Background(), workspace.CreateWorkspaceRequest{Name: "workspace"}, actor)
	require.NoError(t, err)

	workspaces, err := service.ListMyWorkspaces(context.Background(), actor)

	require.NoError(t, err)
	require.Len(t, workspaces, 1)
}

func TestWorkspaceServiceAddMemberPermission(t *testing.T) {
	repo := newFakeWorkspaceRepo()
	service := workspace.NewService(repo, nil)
	owner := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	ws, err := service.CreateWorkspace(context.Background(), workspace.CreateWorkspaceRequest{Name: "workspace"}, owner)
	require.NoError(t, err)

	memberID := uuid.New()
	require.NoError(t, service.AddMember(context.Background(), ws.ID, memberID, workspace.RoleReviewer, owner))
	role, err := repo.GetMemberRole(context.Background(), ws.ID, memberID)
	require.NoError(t, err)
	require.Equal(t, workspace.RoleReviewer, role)

	nonOwner := auth.Principal{UserID: memberID, Roles: []string{auth.RoleReviewer}}
	err = service.AddMember(context.Background(), ws.ID, uuid.New(), workspace.RoleViewer, nonOwner)
	require.Error(t, err)
}
