package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestWorkerFillFormHandlerUsesPythonRunnerNotPlaceholder(t *testing.T) {
	runDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{
		RunID:      uuid.New(),
		OutDir:     runDir,
		Manifest:   manifest,
		Validation: &pythonpkg.ArtifactValidationResult{RunDir: runDir, OK: true},
	}}
	registrar := &fakeArtifactRegistrar{}
	eventRepo := &fakeRunEventRepo{}
	eventService := runevent.NewService(eventRepo, nil)
	repo := newFakeJobRepo()
	service := jobs.NewService(repo, eventService, nil, &fakeAuthorizer{}, nil, zap.NewNop(), 1)
	cfg := workerTestConfig()
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterHandler(jobs.JobTypeNoop, jobs.NewNoopHandler(eventService))
	worker.RegisterHandler(jobs.JobTypeFillForm, jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(registrar, zap.NewNop()), eventService, zap.NewNop()))
	worker.RegisterHandler(jobs.JobTypeIngestKnowledge, jobs.NewIngestKnowledgePythonHandler(runner, eventService, zap.NewNop(), false))
	worker.RegisterHandler(jobs.JobTypeArchiveArtifacts, jobs.NewPlaceholderHandler(jobs.JobTypeArchiveArtifacts))
	job := fillFormJob(runDir)
	job.Status = jobs.JobStatusQueued
	job.ResourceType = jobs.ResourceTypeFillRun
	job.MaxAttempts = 1
	repo.add(job)

	err := worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeFillForm), mustTaskPayload(t, job.ID)))

	require.NoError(t, err)
	require.Len(t, runner.Step15Calls, 1)
	require.Len(t, registrar.requests, 2)
	require.ElementsMatch(t, []string{"run_manifest", "predictions"}, artifactTypesFromRequests(registrar.requests))
	updated, getErr := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, getErr)
	require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
	require.NotContains(t, updated.ErrorMessage, "not implemented")
	requireEventTypes(t, eventRepo, runevent.EventPythonStarted, runevent.EventPythonFinished, runevent.EventArtifactsRegistered, runevent.EventSucceeded)
}

func TestWorkerIngestDisabledHandlerReturnsNotImplemented(t *testing.T) {
	handler := jobs.NewIngestKnowledgePythonHandler(&pythonpkg.FakeRunner{}, nil, zap.NewNop(), false)
	job := jobs.Job{
		ID:          uuid.New(),
		WorkspaceID: uuid.New(),
		ResourceID:  uuid.New(),
		JobType:     jobs.JobTypeIngestKnowledge,
		Payload:     map[string]any{},
	}

	err := handler.Handle(context.Background(), &job)

	require.ErrorIs(t, err, jobs.ErrHandlerNotImplemented)
}
