package knowledge

import (
	"context"
	"time"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type IngestionLifecycleAdapter struct {
	Ingestions IngestionJobRepo
	Versions   KnowledgeIndexVersionRepo
	Bases      KnowledgeBaseRepo
	Documents  KnowledgeDocumentRepo
	Logger     *zap.Logger
}

func NewIngestionLifecycleAdapter(ingestions IngestionJobRepo, versions KnowledgeIndexVersionRepo, bases KnowledgeBaseRepo, documents KnowledgeDocumentRepo, logger *zap.Logger) *IngestionLifecycleAdapter {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &IngestionLifecycleAdapter{Ingestions: ingestions, Versions: versions, Bases: bases, Documents: documents, Logger: logger}
}

func (l *IngestionLifecycleAdapter) MarkIngestionRunning(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID) error {
	_ = jobID
	if l == nil || l.Ingestions == nil || ingestionJobID == uuid.Nil {
		return nil
	}
	startedAt := time.Now().UTC()
	if err := l.Ingestions.MarkRunning(ctx, ingestionJobID, startedAt); err != nil {
		return err
	}
	if ingestion, err := l.Ingestions.GetByID(ctx, ingestionJobID); err == nil {
		if l.Bases != nil {
			_ = l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusBuilding)
		}
		if l.Documents != nil {
			l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusIndexing, "")
		}
	}
	return nil
}

func (l *IngestionLifecycleAdapter) MarkIngestionSucceeded(ctx context.Context, ingestionJobID uuid.UUID, result *pythonpkg.IngestionResult) error {
	if l == nil || l.Ingestions == nil || ingestionJobID == uuid.Nil {
		return nil
	}
	ingestion, err := l.Ingestions.GetByID(ctx, ingestionJobID)
	if err != nil {
		return err
	}
	finishedAt := time.Now().UTC()
	if err := l.Ingestions.MarkSucceeded(ctx, ingestionJobID, finishedAt, 100); err != nil {
		return err
	}
	documentCount := ingestion.DocumentCount
	if documentCount == 0 && l.Documents != nil {
		if docs, err := l.Documents.ListActiveByKnowledgeBase(ctx, ingestion.KnowledgeBaseID); err == nil {
			documentCount = len(docs)
		}
	}
	artifactDir := ""
	manifestPath := ""
	if result != nil {
		artifactDir = result.OutDir
		manifestPath = result.ManifestPath
	}
	if l.Versions != nil && ingestion.IndexVersionID != nil {
		if err := l.Versions.MarkReady(ctx, *ingestion.IndexVersionID, artifactDir, manifestPath, documentCount, 0, finishedAt); err != nil {
			return err
		}
		_ = l.Versions.ArchiveOldVersions(ctx, ingestion.KnowledgeBaseID, *ingestion.IndexVersionID)
	}
	if l.Bases != nil && ingestion.IndexVersionID != nil {
		if err := l.Bases.UpdateCurrentIndexVersion(ctx, ingestion.KnowledgeBaseID, *ingestion.IndexVersionID); err != nil {
			return err
		}
	}
	l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusIndexed, "")
	return nil
}

func (l *IngestionLifecycleAdapter) MarkIngestionFailed(ctx context.Context, ingestionJobID uuid.UUID, err error) error {
	if l == nil || l.Ingestions == nil || ingestionJobID == uuid.Nil {
		return nil
	}
	ingestion, getErr := l.Ingestions.GetByID(ctx, ingestionJobID)
	if getErr != nil {
		return getErr
	}
	errMsg := ""
	if err != nil {
		errMsg = err.Error()
	}
	failedAt := time.Now().UTC()
	if markErr := l.Ingestions.MarkFailed(ctx, ingestionJobID, failedAt, errMsg); markErr != nil {
		return markErr
	}
	if l.Versions != nil && ingestion.IndexVersionID != nil {
		_ = l.Versions.MarkFailed(ctx, *ingestion.IndexVersionID, errMsg, failedAt)
	}
	if l.Bases != nil {
		_ = l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusFailed)
	}
	l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusUploaded, "")
	return nil
}

func (l *IngestionLifecycleAdapter) MarkIngestionCanceled(ctx context.Context, ingestionJobID uuid.UUID) error {
	if l == nil || l.Ingestions == nil || ingestionJobID == uuid.Nil {
		return nil
	}
	ingestion, err := l.Ingestions.GetByID(ctx, ingestionJobID)
	if err != nil {
		return err
	}
	finishedAt := time.Now().UTC()
	if err := l.Ingestions.MarkCanceled(ctx, ingestionJobID, finishedAt); err != nil {
		return err
	}
	if l.Versions != nil && ingestion.IndexVersionID != nil {
		_ = l.Versions.MarkFailed(ctx, *ingestion.IndexVersionID, "canceled", finishedAt)
	}
	if l.Bases != nil {
		_ = l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusStale)
	}
	l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusUploaded, "")
	return nil
}

func (l *IngestionLifecycleAdapter) markDocuments(ctx context.Context, kbID uuid.UUID, status string, errMsg string) {
	if l == nil || l.Documents == nil || kbID == uuid.Nil {
		return
	}
	docs, err := l.Documents.ListActiveByKnowledgeBase(ctx, kbID)
	if err != nil {
		l.Logger.Warn("list knowledge documents for lifecycle failed", zap.String("knowledge_base_id", kbID.String()), zap.Error(err))
		return
	}
	for _, doc := range docs {
		if err := l.Documents.MarkStatus(ctx, doc.ID, status, errMsg); err != nil {
			l.Logger.Warn("mark knowledge document status failed", zap.String("document_id", doc.ID.String()), zap.String("status", status), zap.Error(err))
		}
	}
}
