package tests

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestKnowledgeBaseServiceCreateSuccess(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	versions := newFakeKnowledgeIndexVersionRepo()
	audits := &fakeAuditRepo{}
	workspaceID := uuid.New()
	service := knowledgepkg.NewKnowledgeBaseService(repo, versions, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop())

	kb, err := service.CreateKnowledgeBase(context.Background(), knowledgepkg.CreateKnowledgeBaseRequest{WorkspaceID: workspaceID, Name: "西咸4号楼知识库"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.NoError(t, err)
	require.Equal(t, workspaceID, kb.WorkspaceID)
	require.Equal(t, "西咸4号楼知识库", kb.Name)
	require.NotEmpty(t, kb.QdrantCollection)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "knowledge_base.created", audits.logs[0].Action)
}

func TestKnowledgeBaseServiceDuplicateNameConflict(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	service := knowledgepkg.NewKnowledgeBaseService(repo, newFakeKnowledgeIndexVersionRepo(), &fakeAuthorizer{}, nil, zap.NewNop())
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	_, err := service.CreateKnowledgeBase(context.Background(), knowledgepkg.CreateKnowledgeBaseRequest{WorkspaceID: workspaceID, Name: "kb"}, actor)
	require.NoError(t, err)

	_, err = service.CreateKnowledgeBase(context.Background(), knowledgepkg.CreateKnowledgeBaseRequest{WorkspaceID: workspaceID, Name: "kb"}, actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeConflict, httpx.ErrorFrom(err).Code)
}

func TestKnowledgeBaseServiceCreateRequiresWorkspaceWrite(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	service := knowledgepkg.NewKnowledgeBaseService(repo, newFakeKnowledgeIndexVersionRepo(), &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop())

	_, err := service.CreateKnowledgeBase(context.Background(), knowledgepkg.CreateKnowledgeBaseRequest{WorkspaceID: uuid.New(), Name: "kb"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.Error(t, err)
	require.Empty(t, repo.bases)
}

func TestKnowledgeBaseServiceCreateRequiresAdmin(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	service := knowledgepkg.NewKnowledgeBaseService(repo, newFakeKnowledgeIndexVersionRepo(), &fakeAuthorizer{}, nil, zap.NewNop())

	_, err := service.CreateKnowledgeBase(context.Background(), knowledgepkg.CreateKnowledgeBaseRequest{WorkspaceID: uuid.New(), Name: "kb"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})

	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
	require.Empty(t, repo.bases)
}

func TestKnowledgeBaseServiceGetListRequiresAdmin(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	authorizer := &fakeAuthorizer{}
	service := knowledgepkg.NewKnowledgeBaseService(repo, newFakeKnowledgeIndexVersionRepo(), authorizer, nil, zap.NewNop())
	admin := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}

	_, err := service.GetKnowledgeBase(context.Background(), kbID, admin)
	require.NoError(t, err)
	items, err := service.ListKnowledgeBases(context.Background(), workspaceID, 50, 0, admin)

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, 2, authorizer.reads)

	_, err = service.GetKnowledgeBase(context.Background(), kbID, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)

	_, err = service.ListKnowledgeBases(context.Background(), workspaceID, 50, 0, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
}

func TestKnowledgeBaseServiceOptionsAllowWorkspaceReaders(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	authorizer := &fakeAuthorizer{}
	service := knowledgepkg.NewKnowledgeBaseService(repo, newFakeKnowledgeIndexVersionRepo(), authorizer, nil, zap.NewNop())

	items, err := service.ListKnowledgeBaseOptions(context.Background(), workspaceID, 50, 0, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, 1, authorizer.reads)
}

func TestKnowledgeBaseServiceListIndexVersionsRequiresAdmin(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	versions := newFakeKnowledgeIndexVersionRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	versionID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, versions.Create(context.Background(), knowledgepkg.KnowledgeIndexVersion{ID: versionID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, Status: knowledgepkg.IndexVersionStatusReady}))
	service := knowledgepkg.NewKnowledgeBaseService(repo, versions, &fakeAuthorizer{}, nil, zap.NewNop())

	items, err := service.ListIndexVersions(context.Background(), kbID, 50, 0, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})
	require.NoError(t, err)
	require.Len(t, items, 1)

	_, err = service.ListIndexVersions(context.Background(), kbID, 50, 0, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
}

func TestKnowledgeBaseServiceSetCurrentIndexVersionRequiresReadyVersion(t *testing.T) {
	repo := newFakeKnowledgeBaseRepo()
	versions := newFakeKnowledgeIndexVersionRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	readyID := uuid.New()
	buildingID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, versions.Create(context.Background(), knowledgepkg.KnowledgeIndexVersion{ID: buildingID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, Status: knowledgepkg.IndexVersionStatusBuilding}))
	require.NoError(t, versions.Create(context.Background(), knowledgepkg.KnowledgeIndexVersion{ID: readyID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, Status: knowledgepkg.IndexVersionStatusReady}))
	service := knowledgepkg.NewKnowledgeBaseService(repo, versions, &fakeAuthorizer{}, nil, zap.NewNop())

	_, err := service.SetCurrentIndexVersion(context.Background(), kbID, buildingID, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeConflict, httpx.ErrorFrom(err).Code)

	kb, err := service.SetCurrentIndexVersion(context.Background(), kbID, readyID, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})
	require.NoError(t, err)
	require.NotNil(t, kb.CurrentIndexVersionID)
	require.Equal(t, readyID, *kb.CurrentIndexVersionID)
}
