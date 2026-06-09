package workspace

import (
	"context"
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

type Repository interface {
	Create(ctx context.Context, workspace Workspace) error
	GetByID(ctx context.Context, id uuid.UUID) (*Workspace, error)
	ListByUser(ctx context.Context, userID uuid.UUID) ([]Workspace, error)
	ListAll(ctx context.Context) ([]Workspace, error)
	AddMember(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID, role string) error
	GetMemberRole(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID) (string, error)
	ListMembers(ctx context.Context, workspaceID uuid.UUID) ([]WorkspaceMemberView, error)
}

type Service struct {
	repo       Repository
	audit      *audit.Service
	authorizer *Authorizer
}

func NewService(repo Repository, auditSvc *audit.Service) *Service {
	return &Service{repo: repo, audit: auditSvc, authorizer: NewAuthorizer(repo)}
}

func (s *Service) CreateWorkspace(ctx context.Context, req CreateWorkspaceRequest, actor auth.Principal) (*Workspace, error) {
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace name is required", http.StatusBadRequest, nil, nil)
	}
	ws := Workspace{
		ID:          uuid.New(),
		Name:        name,
		Description: strings.TrimSpace(req.Description),
		CreatedBy:   actor.UserID,
	}
	if err := s.repo.Create(ctx, ws); err != nil {
		return nil, err
	}
	if err := s.repo.AddMember(ctx, ws.ID, actor.UserID, RoleOwner); err != nil {
		return nil, err
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &ws.ID,
		UserID:       &actor.UserID,
		Action:       "workspace.created",
		ResourceType: "workspace",
		ResourceID:   ws.ID.String(),
		Payload:      map[string]any{"name": ws.Name},
	})
	created, err := s.repo.GetByID(ctx, ws.ID)
	if err != nil {
		return &ws, nil
	}
	return created, nil
}

func (s *Service) ListMyWorkspaces(ctx context.Context, actor auth.Principal) ([]Workspace, error) {
	if auth.IsAdminRoles(actor.Roles) {
		return s.repo.ListAll(ctx)
	}
	return s.repo.ListByUser(ctx, actor.UserID)
}

func (s *Service) GetWorkspace(ctx context.Context, id uuid.UUID, actor auth.Principal) (*Workspace, error) {
	if err := s.ensureWorkspaceMember(ctx, id, actor); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, id)
}

func (s *Service) AddMember(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID, role string, actor auth.Principal) error {
	if !auth.ValidWorkspaceRole(role) {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid workspace role", http.StatusBadRequest, map[string]string{"role": role}, nil)
	}
	if !auth.IsAdminRoles(actor.Roles) {
		memberRole, err := s.repo.GetMemberRole(ctx, workspaceID, actor.UserID)
		if err != nil || memberRole != RoleOwner {
			return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
		}
	}
	if err := s.repo.AddMember(ctx, workspaceID, userID, role); err != nil {
		return err
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &workspaceID,
		UserID:       &actor.UserID,
		Action:       "workspace.member_added",
		ResourceType: "workspace_member",
		ResourceID:   userID.String(),
		Payload:      map[string]any{"role": role},
	})
	return nil
}

func (s *Service) ListMembers(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) ([]WorkspaceMemberView, error) {
	if err := s.ensureWorkspaceMember(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListMembers(ctx, workspaceID)
}

func (s *Service) CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	return s.authorizer.CanReadWorkspace(ctx, workspaceID, actor)
}

func (s *Service) CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	return s.authorizer.CanWriteWorkspace(ctx, workspaceID, actor)
}

func (s *Service) ensureWorkspaceMember(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	if _, err := s.repo.GetMemberRole(ctx, workspaceID, actor.UserID); err != nil {
		return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	return nil
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}
