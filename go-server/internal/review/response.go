package review

import (
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
)

type ReviewItemListResponse struct {
	Items  []ReviewItem `json:"items"`
	Counts ReviewCounts `json:"counts"`
}

type ReviewActionRequest struct {
	Comment string `json:"comment"`
	Reason  string `json:"reason"`
}

type ReviewEditRequest struct {
	EditedAnswer string `json:"edited_answer"`
	Comment      string `json:"comment"`
}

type FillRunResultResponse struct {
	Run          *form.FillRun          `json:"run"`
	Artifacts    []artifact.RunArtifact `json:"artifacts"`
	ReviewCounts ReviewCounts           `json:"review_counts"`
	Downloads    map[string]string      `json:"downloads"`
}
