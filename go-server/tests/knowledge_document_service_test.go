package tests

import (
	"context"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestKnowledgeDocumentServiceUploadSuccess(t *testing.T) {
	bases := newFakeKnowledgeBaseRepo()
	docs := newFakeKnowledgeDocumentRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	files := &fakeFileUploader{file: &filepkg.File{ID: uuid.New(), WorkspaceID: workspaceID, Filename: "doc.xlsx", FileCategory: filepkg.FileCategoryKnowledgeDocument, Status: filepkg.FileStatusActive}}
	audits := &fakeAuditRepo{}
	service := knowledgepkg.NewKnowledgeDocumentService(bases, docs, files, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop())

	doc, err := service.UploadDocument(context.Background(), knowledgepkg.UploadDocumentRequest{KnowledgeBaseID: kbID, OriginalFilename: "doc.xlsx", Size: 4, MIMEType: "application/octet-stream", Reader: strings.NewReader("data"), DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "xixian_4"}, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, kbID, doc.KnowledgeBaseID)
	require.Equal(t, knowledgepkg.KnowledgeDocumentStatusUploaded, doc.Status)
	require.Len(t, files.uploaded, 1)
	require.Equal(t, filepkg.FileCategoryKnowledgeDocument, files.uploaded[0].Category)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "knowledge_document.uploaded", audits.logs[0].Action)
}

func TestKnowledgeDocumentServiceValidatesRoleAndNamespace(t *testing.T) {
	bases := newFakeKnowledgeBaseRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	service := knowledgepkg.NewKnowledgeDocumentService(bases, newFakeKnowledgeDocumentRepo(), &fakeFileUploader{}, &fakeAuthorizer{}, nil, zap.NewNop())

	_, err := service.UploadDocument(context.Background(), knowledgepkg.UploadDocumentRequest{KnowledgeBaseID: kbID, DocumentRole: "bad", Namespace: "ns", Reader: strings.NewReader("x")}, auth.Principal{UserID: uuid.New()})
	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)

	_, err = service.UploadDocument(context.Background(), knowledgepkg.UploadDocumentRequest{KnowledgeBaseID: kbID, DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Reader: strings.NewReader("x")}, auth.Principal{UserID: uuid.New()})
	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)
}

func TestKnowledgeDocumentServiceDeleteAndList(t *testing.T) {
	bases := newFakeKnowledgeBaseRepo()
	docs := newFakeKnowledgeDocumentRepo()
	workspaceID := uuid.New()
	kbID := uuid.New()
	docID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, docs.Create(context.Background(), knowledgepkg.KnowledgeDocument{ID: docID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "doc.xlsx", DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "ns", Status: knowledgepkg.KnowledgeDocumentStatusUploaded}))
	service := knowledgepkg.NewKnowledgeDocumentService(bases, docs, &fakeFileUploader{}, &fakeAuthorizer{}, nil, zap.NewNop())

	items, err := service.ListDocuments(context.Background(), kbID, "", 50, 0, auth.Principal{UserID: uuid.New()})
	require.NoError(t, err)
	require.Len(t, items, 1)

	err = service.DeleteDocument(context.Background(), docID, auth.Principal{UserID: uuid.New()})
	require.NoError(t, err)
	deleted, err := docs.GetByID(context.Background(), docID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.KnowledgeDocumentStatusDeleted, deleted.Status)
}
