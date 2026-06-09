package tests

import (
	"context"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/workspace"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

type fakeUserRepo struct {
	mu         sync.Mutex
	users      map[uuid.UUID]auth.User
	byUsername map[string]uuid.UUID
	roles      map[uuid.UUID][]string
}

func newFakeUserRepo() *fakeUserRepo {
	return &fakeUserRepo{
		users:      make(map[uuid.UUID]auth.User),
		byUsername: make(map[string]uuid.UUID),
		roles:      make(map[uuid.UUID][]string),
	}
}

func (f *fakeUserRepo) addUser(user auth.User, roles []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.users[user.ID] = user
	f.byUsername[user.Username] = user.ID
	f.roles[user.ID] = append([]string(nil), roles...)
}

func (f *fakeUserRepo) Create(ctx context.Context, user auth.User) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.byUsername[user.Username]; ok {
		return httpx.NewAppError(httpx.CodeConflict, "user already exists", http.StatusConflict, nil, nil)
	}
	f.users[user.ID] = user
	f.byUsername[user.Username] = user.ID
	return nil
}

func (f *fakeUserRepo) GetByID(ctx context.Context, id uuid.UUID) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	user, ok := f.users[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "user not found", http.StatusNotFound, nil, nil)
	}
	return &user, nil
}

func (f *fakeUserRepo) GetByUsername(ctx context.Context, username string) (*auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	id, ok := f.byUsername[username]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "user not found", http.StatusNotFound, nil, nil)
	}
	user := f.users[id]
	return &user, nil
}

func (f *fakeUserRepo) List(ctx context.Context, limit int, offset int) ([]auth.User, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	users := make([]auth.User, 0, len(f.users))
	for _, user := range f.users {
		users = append(users, user)
	}
	return users, nil
}

func (f *fakeUserRepo) SetStatus(ctx context.Context, id uuid.UUID, status string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	user, ok := f.users[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "user not found", http.StatusNotFound, nil, nil)
	}
	user.Status = status
	f.users[id] = user
	return nil
}

func (f *fakeUserRepo) AssignRole(ctx context.Context, userID uuid.UUID, roleName string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.users[userID]; !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "user not found", http.StatusNotFound, nil, nil)
	}
	for _, role := range f.roles[userID] {
		if role == roleName {
			return nil
		}
	}
	f.roles[userID] = append(f.roles[userID], roleName)
	return nil
}

func (f *fakeUserRepo) ListRoleNames(ctx context.Context, userID uuid.UUID) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.roles[userID]...), nil
}

func (f *fakeUserRepo) EnsureDefaultRoles(ctx context.Context) error {
	return nil
}

func (f *fakeUserRepo) GetByName(ctx context.Context, name string) (*auth.Role, error) {
	if !auth.ValidGlobalRole(name) {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "role not found", http.StatusNotFound, nil, nil)
	}
	return &auth.Role{ID: uuid.New(), Name: name}, nil
}

type fakeRefreshRepo struct {
	mu     sync.Mutex
	tokens map[string]*auth.RefreshToken
}

func newFakeRefreshRepo() *fakeRefreshRepo {
	return &fakeRefreshRepo{tokens: make(map[string]*auth.RefreshToken)}
}

func (f *fakeRefreshRepo) Create(ctx context.Context, token auth.RefreshToken) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	copy := token
	f.tokens[token.TokenHash] = &copy
	return nil
}

func (f *fakeRefreshRepo) GetByHash(ctx context.Context, hash string) (*auth.RefreshToken, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	token, ok := f.tokens[hash]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "refresh token not found", http.StatusNotFound, nil, nil)
	}
	copy := *token
	return &copy, nil
}

func (f *fakeRefreshRepo) Revoke(ctx context.Context, id uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := time.Now().UTC()
	for _, token := range f.tokens {
		if token.ID == id {
			token.RevokedAt = &now
			return nil
		}
	}
	return httpx.NewAppError(httpx.CodeNotFound, "refresh token not found", http.StatusNotFound, nil, nil)
}

func (f *fakeRefreshRepo) RevokeAllForUser(ctx context.Context, userID uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := time.Now().UTC()
	for _, token := range f.tokens {
		if token.UserID == userID {
			token.RevokedAt = &now
		}
	}
	return nil
}

type fakeWorkspaceRepo struct {
	mu         sync.Mutex
	workspaces map[uuid.UUID]workspace.Workspace
	members    map[uuid.UUID]map[uuid.UUID]string
}

func newFakeWorkspaceRepo() *fakeWorkspaceRepo {
	return &fakeWorkspaceRepo{
		workspaces: make(map[uuid.UUID]workspace.Workspace),
		members:    make(map[uuid.UUID]map[uuid.UUID]string),
	}
}

func (f *fakeWorkspaceRepo) Create(ctx context.Context, ws workspace.Workspace) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.workspaces[ws.ID] = ws
	return nil
}

func (f *fakeWorkspaceRepo) GetByID(ctx context.Context, id uuid.UUID) (*workspace.Workspace, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	ws, ok := f.workspaces[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "workspace not found", http.StatusNotFound, nil, nil)
	}
	return &ws, nil
}

func (f *fakeWorkspaceRepo) ListByUser(ctx context.Context, userID uuid.UUID) ([]workspace.Workspace, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []workspace.Workspace
	for workspaceID, members := range f.members {
		if _, ok := members[userID]; ok {
			out = append(out, f.workspaces[workspaceID])
		}
	}
	return out, nil
}

func (f *fakeWorkspaceRepo) ListAll(ctx context.Context) ([]workspace.Workspace, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []workspace.Workspace
	for _, ws := range f.workspaces {
		out = append(out, ws)
	}
	return out, nil
}

func (f *fakeWorkspaceRepo) AddMember(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID, role string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.members[workspaceID] == nil {
		f.members[workspaceID] = make(map[uuid.UUID]string)
	}
	f.members[workspaceID][userID] = role
	return nil
}

func (f *fakeWorkspaceRepo) GetMemberRole(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if role, ok := f.members[workspaceID][userID]; ok {
		return role, nil
	}
	return "", httpx.NewAppError(httpx.CodeNotFound, "workspace membership not found", http.StatusNotFound, nil, nil)
}

func (f *fakeWorkspaceRepo) ListMembers(ctx context.Context, workspaceID uuid.UUID) ([]workspace.WorkspaceMemberView, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []workspace.WorkspaceMemberView
	for userID, role := range f.members[workspaceID] {
		out = append(out, workspace.WorkspaceMemberView{WorkspaceID: workspaceID, UserID: userID, Role: role})
	}
	return out, nil
}

func requireAppError(t *testing.T, err error, code string, status int) {
	t.Helper()
	require.Error(t, err)
	appErr := httpx.ErrorFrom(err)
	require.Equal(t, code, appErr.Code)
	require.Equal(t, status, appErr.HTTPStatus)
}
