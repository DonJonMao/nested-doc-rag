package middleware

import (
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
)

func RequireRoles(roles ...string) func(http.Handler) http.Handler {
	return auth.RequireRoles(roles...)
}

func RequireWorkspaceRole(reader auth.WorkspaceRoleReader, workspaceIDParam string, allowedRoles ...string) func(http.Handler) http.Handler {
	return auth.RequireWorkspaceRole(reader, workspaceIDParam, allowedRoles...)
}
