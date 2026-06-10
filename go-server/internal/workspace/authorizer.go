package workspace

import (
	"context"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

type Authorizer struct {
	repo Repository
}

func NewAuthorizer(repo Repository) *Authorizer {
	return &Authorizer{repo: repo}
}

func (a *Authorizer) CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	if _, err := a.repo.GetMemberRole(ctx, workspaceID, actor.UserID); err != nil {
		return forbidden()
	}
	return nil
}

func (a *Authorizer) CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	memberRole, err := a.repo.GetMemberRole(ctx, workspaceID, actor.UserID)
	if err != nil {
		return forbidden()
	}
	if memberRole != RoleOwner && memberRole != RoleOperator {
		return forbidden()
	}
	return nil
}

func (a *Authorizer) CanReviewWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	memberRole, err := a.repo.GetMemberRole(ctx, workspaceID, actor.UserID)
	if err != nil {
		return forbidden()
	}
	if memberRole != RoleOwner && memberRole != RoleOperator && memberRole != RoleReviewer {
		return forbidden()
	}
	return nil
}

func forbidden() error {
	return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
}
