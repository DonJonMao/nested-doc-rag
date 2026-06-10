package review

import (
	"context"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type RunEventWriter interface {
	Create(ctx context.Context, event runevent.RunEvent) (*runevent.RunEvent, error)
}

type Importer struct {
	Repo   Repo
	Parser *ArtifactReviewParser
	Events RunEventWriter
	Logger *zap.Logger
}

func NewImporter(repo Repo, parser *ArtifactReviewParser, events RunEventWriter, logger *zap.Logger) *Importer {
	if parser == nil {
		parser = &ArtifactReviewParser{}
	}
	if logger == nil {
		logger = zap.NewNop()
	}
	return &Importer{Repo: repo, Parser: parser, Events: events, Logger: logger}
}

func (i *Importer) ImportForFillRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, manifest *python.RunManifest) (ReviewImportResult, error) {
	var result ReviewImportResult
	if i == nil || i.Repo == nil {
		return result, nil
	}
	paths := pathsFromManifest(manifest)
	if paths == (ReviewArtifactPaths{}) {
		i.Logger.Warn("review artifacts missing from manifest", zap.String("run_id", runID.String()))
		return result, nil
	}
	before, _ := i.Repo.CountByRun(ctx, runID)
	items, err := i.Parser.ParseReviewItems(ctx, runID, workspaceID, paths)
	result.ParseErrors = i.Parser.LastParseErrors()
	if err != nil {
		return result, err
	}
	result.TotalParsed = len(items)
	for _, item := range items {
		if item.WorkspaceID == uuid.Nil {
			item.WorkspaceID = workspaceID
		}
		if item.RunID == uuid.Nil {
			item.RunID = runID
		}
		if item.ReviewRequired {
			result.ReviewRequired++
		}
		if item.WritebackAllowed {
			result.WritebackAllowed++
		}
		if err := i.Repo.UpsertByRunAndField(ctx, item); err != nil {
			return result, err
		}
	}
	after, _ := i.Repo.CountByRun(ctx, runID)
	if after.Total > before.Total {
		result.Created = after.Total - before.Total
	}
	if result.Created < result.TotalParsed {
		result.Updated = result.TotalParsed - result.Created
	}
	if i.Events != nil {
		_, _ = i.Events.Create(ctx, runevent.RunEvent{
			WorkspaceID: workspaceID,
			RunID:       runID,
			EventType:   runevent.EventReviewItemsImported,
			Payload: map[string]any{
				"total_parsed":      result.TotalParsed,
				"created":           result.Created,
				"updated":           result.Updated,
				"parse_errors":      result.ParseErrors,
				"review_required":   result.ReviewRequired,
				"writeback_allowed": result.WritebackAllowed,
			},
		})
	}
	return result, nil
}

func pathsFromManifest(manifest *python.RunManifest) ReviewArtifactPaths {
	var paths ReviewArtifactPaths
	if manifest == nil {
		return paths
	}
	if path, ok := manifest.ArtifactPath(artifact.TypeReviewItems); ok {
		paths.ReviewItemsPath = path
	}
	if path, ok := manifest.ArtifactPath(artifact.TypePredictionsRaw); ok {
		paths.PredictionsRawPath = path
	}
	if path, ok := manifest.ArtifactPath(artifact.TypeAgentOverlays); ok {
		paths.AgentOverlaysPath = path
	}
	if path, ok := manifest.ArtifactPath(artifact.TypePredictionsAgentView); ok {
		paths.PredictionsAgentViewPath = path
	}
	return paths
}
