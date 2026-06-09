package auth

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

type UserRepository interface {
	Create(ctx context.Context, user User) error
	GetByID(ctx context.Context, id uuid.UUID) (*User, error)
	GetByUsername(ctx context.Context, username string) (*User, error)
	List(ctx context.Context, limit int, offset int) ([]User, error)
	SetStatus(ctx context.Context, id uuid.UUID, status string) error
	AssignRole(ctx context.Context, userID uuid.UUID, roleName string) error
	ListRoleNames(ctx context.Context, userID uuid.UUID) ([]string, error)
}

type RoleRepository interface {
	EnsureDefaultRoles(ctx context.Context) error
	GetByName(ctx context.Context, name string) (*Role, error)
}

type RefreshTokenRepository interface {
	Create(ctx context.Context, token RefreshToken) error
	GetByHash(ctx context.Context, hash string) (*RefreshToken, error)
	Revoke(ctx context.Context, id uuid.UUID) error
	RevokeAllForUser(ctx context.Context, userID uuid.UUID) error
}

type Service struct {
	users      UserRepository
	roles      RoleRepository
	refresh    RefreshTokenRepository
	tokens     *TokenManager
	refreshTTL time.Duration
	audit      *audit.Service
}

func NewService(
	users UserRepository,
	roles RoleRepository,
	refresh RefreshTokenRepository,
	tokens *TokenManager,
	refreshTTL time.Duration,
	auditSvc *audit.Service,
) *Service {
	return &Service{
		users:      users,
		roles:      roles,
		refresh:    refresh,
		tokens:     tokens,
		refreshTTL: refreshTTL,
		audit:      auditSvc,
	}
}

func (s *Service) Login(ctx context.Context, username string, password string, ip string, userAgent string) (*LoginResult, error) {
	username = strings.TrimSpace(username)
	user, err := s.users.GetByUsername(ctx, username)
	if err != nil || user == nil {
		s.recordLoginFailed(ctx, nil, username, ip, userAgent)
		return nil, unauthorizedLogin()
	}
	if user.Status != UserStatusActive {
		s.recordLoginFailed(ctx, &user.ID, username, ip, userAgent)
		return nil, httpx.NewAppError(httpx.CodeForbidden, "user is disabled", http.StatusForbidden, nil, nil)
	}
	if !VerifyPassword(user.PasswordHash, password) {
		s.recordLoginFailed(ctx, &user.ID, username, ip, userAgent)
		return nil, unauthorizedLogin()
	}
	roles, err := s.users.ListRoleNames(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	result, err := s.issueTokens(ctx, *user, roles)
	if err != nil {
		return nil, err
	}
	s.record(ctx, audit.AuditLog{
		UserID:       &user.ID,
		Action:       "auth.login_success",
		ResourceType: "user",
		ResourceID:   user.ID.String(),
		IP:           ip,
		UserAgent:    userAgent,
		Payload:      map[string]any{"username": username},
	})
	return result, nil
}

func (s *Service) Refresh(ctx context.Context, refreshToken string) (*LoginResult, error) {
	hash := HashRefreshToken(refreshToken)
	stored, err := s.refresh.GetByHash(ctx, hash)
	if err != nil || stored == nil {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "invalid refresh token", http.StatusUnauthorized, nil, nil)
	}
	if stored.RevokedAt != nil || time.Now().UTC().After(stored.ExpiresAt) {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "invalid refresh token", http.StatusUnauthorized, nil, nil)
	}
	if !VerifyRefreshToken(refreshToken, stored.TokenHash) {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "invalid refresh token", http.StatusUnauthorized, nil, nil)
	}
	user, err := s.users.GetByID(ctx, stored.UserID)
	if err != nil {
		return nil, err
	}
	if user.Status != UserStatusActive {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "user is disabled", http.StatusForbidden, nil, nil)
	}
	roles, err := s.users.ListRoleNames(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if err := s.refresh.Revoke(ctx, stored.ID); err != nil {
		return nil, err
	}
	return s.issueTokens(ctx, *user, roles)
}

func (s *Service) Logout(ctx context.Context, refreshToken string) error {
	hash := HashRefreshToken(refreshToken)
	stored, err := s.refresh.GetByHash(ctx, hash)
	if err != nil || stored == nil {
		return httpx.NewAppError(httpx.CodeUnauthorized, "invalid refresh token", http.StatusUnauthorized, nil, nil)
	}
	if err := s.refresh.Revoke(ctx, stored.ID); err != nil {
		return err
	}
	s.record(ctx, audit.AuditLog{
		UserID:       &stored.UserID,
		Action:       "auth.logout",
		ResourceType: "user",
		ResourceID:   stored.UserID.String(),
	})
	return nil
}

func (s *Service) Me(ctx context.Context, userID uuid.UUID) (*MeResult, error) {
	user, err := s.users.GetByID(ctx, userID)
	if err != nil {
		return nil, err
	}
	roles, err := s.users.ListRoleNames(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	return &MeResult{User: UserToView(*user, roles)}, nil
}

func (s *Service) EnsureDefaultRoles(ctx context.Context) error {
	if s.roles == nil {
		return nil
	}
	return s.roles.EnsureDefaultRoles(ctx)
}

func (s *Service) BootstrapAdmin(ctx context.Context, username string, password string) error {
	username = strings.TrimSpace(username)
	if username == "" || strings.TrimSpace(password) == "" {
		return nil
	}
	if _, err := s.users.GetByUsername(ctx, username); err == nil {
		return nil
	}
	hash, err := HashPassword(password)
	if err != nil {
		return err
	}
	admin := User{
		ID:           uuid.New(),
		Username:     username,
		PasswordHash: hash,
		DisplayName:  "Administrator",
		Status:       UserStatusActive,
	}
	if err := s.users.Create(ctx, admin); err != nil {
		return err
	}
	return s.users.AssignRole(ctx, admin.ID, RoleAdmin)
}

func (s *Service) issueTokens(ctx context.Context, user User, roles []string) (*LoginResult, error) {
	accessToken, expiresAt, err := s.tokens.GenerateAccessToken(user, roles)
	if err != nil {
		return nil, err
	}
	plainRefresh, refreshHash, err := GenerateRefreshToken()
	if err != nil {
		return nil, err
	}
	refresh := RefreshToken{
		ID:        uuid.New(),
		UserID:    user.ID,
		TokenHash: refreshHash,
		ExpiresAt: time.Now().UTC().Add(s.refreshTTL),
	}
	if err := s.refresh.Create(ctx, refresh); err != nil {
		return nil, err
	}
	return &LoginResult{
		AccessToken:  accessToken,
		RefreshToken: plainRefresh,
		ExpiresAt:    expiresAt,
		User:         UserToView(user, roles),
	}, nil
}

func (s *Service) recordLoginFailed(ctx context.Context, userID *uuid.UUID, username string, ip string, userAgent string) {
	s.record(ctx, audit.AuditLog{
		UserID:       userID,
		Action:       "auth.login_failed",
		ResourceType: "user",
		IP:           ip,
		UserAgent:    userAgent,
		Payload:      map[string]any{"username": username},
	})
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}

func unauthorizedLogin() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "invalid username or password", http.StatusUnauthorized, nil, nil)
}
