package form

import (
	"context"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type committedJobEnqueuer interface {
	EnqueuePersistedJob(context.Context, *jobs.Job) error
}

func (s *FillRunService) createPinnedFillRun(ctx context.Context, req CreateFillRunRequest, actor auth.Principal) (*FillRun, error) {
	enqueuer, ok := s.jobs.(committedJobEnqueuer)
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeInternal, "committed job enqueuer is not configured", http.StatusInternalServerError, nil, nil)
	}
	formFile, err := s.forms.GetByID(ctx, req.FormFileID)
	if err != nil {
		return nil, err
	}
	if formFile.CreatedBy != actor.UserID {
		return nil, fillRunNotFound()
	}
	if req.WorkspaceID == uuid.Nil {
		req.WorkspaceID = formFile.WorkspaceID
	}
	if req.WorkspaceID != formFile.WorkspaceID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "form file workspace mismatch", http.StatusForbidden, nil, nil)
	}
	name, err := normalizeFillRunName(req.Name, formFile.Filename)
	if err != nil {
		return nil, err
	}
	writeback := true
	if req.Writeback != nil {
		writeback = *req.Writeback
	}
	if !auth.IsAdminRoles(actor.Roles) {
		// Preserve the product API's server-selected options for ordinary users.
		req.Rows = s.cfg.Python.Step15DefaultRows
		req.RetrievalMode = s.cfg.Python.Step15DefaultRetrievalMode
		req.PromptVersion = s.cfg.Python.Step15DefaultPromptVersion
		req.Judge, req.UseJudgeCache, writeback = false, false, true
	}
	id, now := uuid.New(), time.Now().UTC()
	run := FillRun{
		ID: id, WorkspaceID: req.WorkspaceID, FormFileID: req.FormFileID, Name: name,
		RoomContext: strings.TrimSpace(req.RoomContext), RowsSpec: defaultString(req.Rows, s.cfg.Python.Step15DefaultRows),
		RetrievalMode: defaultString(req.RetrievalMode, s.cfg.Python.Step15DefaultRetrievalMode),
		PromptVersion: defaultString(req.PromptVersion, s.cfg.Python.Step15DefaultPromptVersion),
		JudgeEnabled:  req.Judge, UseJudgeCache: req.UseJudgeCache, WritebackEnabled: writeback,
		Status: FillRunStatusCreated, OutDir: filepath.Join(s.cfg.Python.ProjectDir, "artifacts", "runs", id.String()),
		CreatedBy: actor.UserID, CreatedAt: now, UpdatedAt: now,
	}
	created, job, err := s.pinned.CreateFillRunPinned(ctx, run, req, actor)
	if err != nil {
		return nil, err
	}
	if err := enqueuer.EnqueuePersistedJob(ctx, job); err != nil {
		// The committed queued job is recovered by the dispatch loop. Return its
		// stable run ID so a transient Redis failure does not invite duplicate runs.
		s.logger.Warn("fill job dispatch pending recovery", zap.String("job_id", job.ID.String()), zap.Error(err))
	}
	s.record(ctx, actor, created.WorkspaceID, "fill_run.created", "fill_run", created.ID.String(), map[string]any{
		"job_id": job.ID.String(), "form_file_id": created.FormFileID.String(), "name": created.Name,
		"target_scope": created.TargetScope, "global_scope": created.GlobalScope,
	})
	return s.repo.GetByID(ctx, created.ID)
}
