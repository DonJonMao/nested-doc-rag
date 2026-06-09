package auth

import (
	"context"
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type contextKey string

const principalContextKey contextKey = "auth_principal"

type Principal struct {
	UserID   uuid.UUID
	Username string
	Roles    []string
}

type WorkspaceRoleReader interface {
	GetMemberRole(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID) (string, error)
}

func AuthMiddleware(tokens *TokenManager) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			header := strings.TrimSpace(r.Header.Get("Authorization"))
			if !strings.HasPrefix(header, "Bearer ") {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeUnauthorized, "missing bearer token", http.StatusUnauthorized, nil, nil))
				return
			}
			claims, err := tokens.ParseAccessToken(strings.TrimSpace(strings.TrimPrefix(header, "Bearer ")))
			if err != nil {
				httpx.WriteError(w, r, err)
				return
			}
			userID, err := uuid.Parse(claims.UserID)
			if err != nil {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeUnauthorized, "invalid access token", http.StatusUnauthorized, nil, err))
				return
			}
			principal := Principal{UserID: userID, Username: claims.Username, Roles: claims.Roles}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), principalContextKey, principal)))
		})
	}
}

func RequireRoles(roles ...string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !HasAnyRole(RolesFromContext(r.Context()), roles...) {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func RequireWorkspaceRole(reader WorkspaceRoleReader, workspaceIDParam string, allowedRoles ...string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if IsAdmin(r.Context()) {
				next.ServeHTTP(w, r)
				return
			}
			userID, ok := UserIDFromContext(r.Context())
			if !ok {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil))
				return
			}
			workspaceID, err := uuid.Parse(chi.URLParam(r, workspaceIDParam))
			if err != nil {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid workspace id", http.StatusBadRequest, nil, err))
				return
			}
			role, err := reader.GetMemberRole(r.Context(), workspaceID, userID)
			if err != nil || !HasAnyRole([]string{role}, allowedRoles...) {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func ContextWithPrincipal(ctx context.Context, principal Principal) context.Context {
	return context.WithValue(ctx, principalContextKey, principal)
}

func PrincipalFromContext(ctx context.Context) (Principal, bool) {
	principal, ok := ctx.Value(principalContextKey).(Principal)
	return principal, ok
}

func UserIDFromContext(ctx context.Context) (uuid.UUID, bool) {
	principal, ok := PrincipalFromContext(ctx)
	if !ok || principal.UserID == uuid.Nil {
		return uuid.Nil, false
	}
	return principal.UserID, true
}

func RolesFromContext(ctx context.Context) []string {
	principal, ok := PrincipalFromContext(ctx)
	if !ok {
		return nil
	}
	return principal.Roles
}

func IsAdmin(ctx context.Context) bool {
	return IsAdminRoles(RolesFromContext(ctx))
}
