package form

import "github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"

type FormListResponse struct {
	Forms []FormFile `json:"forms"`
}

type FillRunListResponse struct {
	FillRuns []FillRun `json:"fill_runs"`
}

type FillRunArtifactListResponse struct {
	Artifacts []artifact.RunArtifact `json:"artifacts"`
}

type CancelFillRunResponse struct {
	FillRun  *FillRun `json:"fill_run"`
	Canceled bool     `json:"canceled"`
}
