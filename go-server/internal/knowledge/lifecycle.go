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
	buildStore *PGXBuildStore
}

func (l *IngestionLifecycleAdapter) SetBuildStore(store *PGXBuildStore) { l.buildStore = store }

func (l *IngestionLifecycleAdapter) ReadPublishedIngestion(ctx context.Context, id uuid.UUID) (*pythonpkg.IngestionResult, bool, error) {
	if l == nil || l.buildStore == nil {
		return nil, false, buildConflict("versioned publication store is not configured")
	}
	return l.buildStore.ReadPublishedIngestion(ctx, id)
}

func NewIngestionLifecycleAdapter(ingestions IngestionJobRepo, versions KnowledgeIndexVersionRepo, bases KnowledgeBaseRepo, documents KnowledgeDocumentRepo, logger *zap.Logger) *IngestionLifecycleAdapter {
	if logger == nil {
		logger = zap.NewNop()
	}
	l := &IngestionLifecycleAdapter{Ingestions: ingestions, Versions: versions, Bases: bases, Documents: documents, Logger: logger}
	if repo, ok := ingestions.(*PGXIngestionJobRepo); ok {
		l.buildStore = NewPGXBuildStore(repo.pool)
	}
	return l
}

func (l *IngestionLifecycleAdapter) MarkIngestionRunning(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID) error {
	_ = jobID
	if l != nil && l.buildStore != nil {
		return l.buildStore.MarkRunning(ctx, ingestionJobID)
	}
	if l == nil || l.Ingestions == nil || ingestionJobID == uuid.Nil {
		return nil
	}
	startedAt := time.Now().UTC()
	if err := l.Ingestions.MarkRunning(ctx, ingestionJobID, startedAt); err != nil {
		return err
	}
	ingestion, err := l.Ingestions.GetByID(ctx, ingestionJobID)
	if err != nil {
		return err
	}
	if l.Bases != nil {
		if err := l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusBuilding); err != nil {
			return err
		}
	}
	return l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusIndexing, "")
}

func (l *IngestionLifecycleAdapter) MarkIngestionSucceeded(ctx context.Context, ingestionJobID uuid.UUID, result *pythonpkg.IngestionResult) error {
	if l != nil && l.buildStore != nil {
		return l.buildStore.CompleteBuild(ctx, ingestionJobID, result)
	}
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
		docs, err := l.Documents.ListActiveByKnowledgeBase(ctx, ingestion.KnowledgeBaseID)
		if err != nil {
			return err
		}
		documentCount = len(docs)
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
	}
	if l.Bases != nil && ingestion.IndexVersionID != nil {
		if err := l.Bases.UpdateCurrentIndexVersion(ctx, ingestion.KnowledgeBaseID, *ingestion.IndexVersionID); err != nil {
			return err
		}
	}
	return l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusIndexed, "")
}

func (l *IngestionLifecycleAdapter) MarkIngestionFailed(ctx context.Context, ingestionJobID uuid.UUID, err error) error {
	if l != nil && l.buildStore != nil {
		message := ""
		if err != nil {
			message = err.Error()
		}
		return l.buildStore.FailBuild(ctx, ingestionJobID, message, false)
	}
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
		if markErr := l.Versions.MarkFailed(ctx, *ingestion.IndexVersionID, errMsg, failedAt); markErr != nil {
			return markErr
		}
	}
	if l.Bases != nil {
		if markErr := l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusFailed); markErr != nil {
			return markErr
		}
	}
	return l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusUploaded, "")
}

func (l *IngestionLifecycleAdapter) MarkIngestionCanceled(ctx context.Context, ingestionJobID uuid.UUID) error {
	if l != nil && l.buildStore != nil {
		return l.buildStore.FailBuild(ctx, ingestionJobID, "canceled", true)
	}
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
		if err := l.Versions.MarkFailed(ctx, *ingestion.IndexVersionID, "canceled", finishedAt); err != nil {
			return err
		}
	}
	if l.Bases != nil {
		if err := l.Bases.UpdateStatus(ctx, ingestion.KnowledgeBaseID, KnowledgeBaseStatusStale); err != nil {
			return err
		}
	}
	return l.markDocuments(ctx, ingestion.KnowledgeBaseID, KnowledgeDocumentStatusUploaded, "")
}

func (l *IngestionLifecycleAdapter) markDocuments(ctx context.Context, kbID uuid.UUID, status string, errMsg string) error {
	if l == nil || l.Documents == nil || kbID == uuid.Nil {
		return nil
	}
	docs, err := l.Documents.ListActiveByKnowledgeBase(ctx, kbID)
	if err != nil {
		return err
	}
	for _, doc := range docs {
		if err := l.Documents.MarkStatus(ctx, doc.ID, status, errMsg); err != nil {
			return err
		}
	}
	return nil
}
