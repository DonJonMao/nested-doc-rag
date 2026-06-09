package user

import "github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"

type User = auth.User
type Role = auth.Role

type CreateUserRequest struct {
	Username    string   `json:"username"`
	Password    string   `json:"password"`
	DisplayName string   `json:"display_name"`
	Email       string   `json:"email"`
	Roles       []string `json:"roles"`
}

type SetStatusRequest struct {
	Status string `json:"status"`
}

type AssignRoleRequest struct {
	Role string `json:"role"`
}
