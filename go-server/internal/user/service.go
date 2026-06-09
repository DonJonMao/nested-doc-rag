package user

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
	Create(ctx context.Context, user auth.User) error
	GetByID(ctx context.Context, id uuid.UUID) (*auth.User, error)
	GetByUsername(ctx context.Context, username string) (*auth.User, error)
	List(ctx context.Context, limit int, offset int) ([]auth.User, error)
	SetStatus(ctx context.Context, id uuid.UUID, status string) error
	AssignRole(ctx context.Context, userID uuid.UUID, roleName string) error
	ListRoleNames(ctx context.Context, userID uuid.UUID) ([]string, error)
}

type Service struct {
	repo  Repository
	audit *audit.Service
}

func NewService(repo Repository, auditSvc *audit.Service) *Service {
	return &Service{repo: repo, audit: auditSvc}
}

func (s *Service) CreateUser(ctx context.Context, req CreateUserRequest, actor auth.Principal) (*auth.UserView, error) {
	if !auth.IsAdminRoles(actor.Roles) {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	username := strings.TrimSpace(req.Username)
	if username == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "username is required", http.StatusBadRequest, nil, nil)
	}
	hash, err := auth.HashPassword(req.Password)
	if err != nil {
		return nil, err
	}
	roles := req.Roles
	if len(roles) == 0 {
		roles = []string{auth.RoleViewer}
	}
	for _, role := range roles {
		if !auth.ValidGlobalRole(role) {
			return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid role", http.StatusBadRequest, map[string]string{"role": role}, nil)
		}
	}
	newUser := auth.User{
		ID:           uuid.New(),
		Username:     username,
		PasswordHash: hash,
		DisplayName:  strings.TrimSpace(req.DisplayName),
		Email:        strings.TrimSpace(req.Email),
		Status:       auth.UserStatusActive,
	}
	if err := s.repo.Create(ctx, newUser); err != nil {
		return nil, err
	}
	for _, role := range roles {
		if err := s.repo.AssignRole(ctx, newUser.ID, role); err != nil {
			return nil, err
		}
	}
	s.record(ctx, audit.AuditLog{
		UserID:       &actor.UserID,
		Action:       "user.created",
		ResourceType: "user",
		ResourceID:   newUser.ID.String(),
		Payload:      map[string]any{"username": newUser.Username, "roles": roles},
	})
	view := auth.UserToView(newUser, roles)
	return &view, nil
}

func (s *Service) GetUser(ctx context.Context, id uuid.UUID, actor auth.Principal) (*auth.UserView, error) {
	if !auth.IsAdminRoles(actor.Roles) && actor.UserID != id {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	user, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}
	roles, err := s.repo.ListRoleNames(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	view := auth.UserToView(*user, roles)
	return &view, nil
}

func (s *Service) ListUsers(ctx context.Context, actor auth.Principal) ([]auth.UserView, error) {
	if !auth.IsAdminRoles(actor.Roles) {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	users, err := s.repo.List(ctx, 100, 0)
	if err != nil {
		return nil, err
	}
	views := make([]auth.UserView, 0, len(users))
	for _, item := range users {
		roles, err := s.repo.ListRoleNames(ctx, item.ID)
		if err != nil {
			return nil, err
		}
		views = append(views, auth.UserToView(item, roles))
	}
	return views, nil
}

func (s *Service) SetUserStatus(ctx context.Context, id uuid.UUID, status string, actor auth.Principal) error {
	if !auth.IsAdminRoles(actor.Roles) {
		return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	if status != auth.UserStatusActive && status != auth.UserStatusDisabled {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid user status", http.StatusBadRequest, nil, nil)
	}
	if err := s.repo.SetStatus(ctx, id, status); err != nil {
		return err
	}
	s.record(ctx, audit.AuditLog{
		UserID:       &actor.UserID,
		Action:       "user.status_changed",
		ResourceType: "user",
		ResourceID:   id.String(),
		Payload:      map[string]any{"status": status},
	})
	return nil
}

func (s *Service) AssignRole(ctx context.Context, userID uuid.UUID, roleName string, actor auth.Principal) error {
	if !auth.IsAdminRoles(actor.Roles) {
		return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	}
	if !auth.ValidGlobalRole(roleName) {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid role", http.StatusBadRequest, map[string]string{"role": roleName}, nil)
	}
	if err := s.repo.AssignRole(ctx, userID, roleName); err != nil {
		return err
	}
	s.record(ctx, audit.AuditLog{
		UserID:       &actor.UserID,
		Action:       "user.role_assigned",
		ResourceType: "user",
		ResourceID:   userID.String(),
		Payload:      map[string]any{"role": roleName},
	})
	return nil
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}
