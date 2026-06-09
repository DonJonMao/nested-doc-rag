package jobs

import (
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

func CanTransition(from string, to string) bool {
	switch from {
	case JobStatusCreated:
		return to == JobStatusQueued
	case JobStatusQueued:
		return to == JobStatusRunning || to == JobStatusCanceled
	case JobStatusRunning:
		return to == JobStatusSucceeded ||
			to == JobStatusCompletedWithFailures ||
			to == JobStatusFailed ||
			to == JobStatusCancelRequested ||
			to == JobStatusCanceled
	case JobStatusCancelRequested:
		return to == JobStatusCanceled || to == JobStatusFailed
	case JobStatusFailed:
		return to == JobStatusQueued
	case JobStatusCompletedWithFailures:
		return to == JobStatusQueued
	default:
		return false
	}
}

func ValidateTransition(from string, to string) error {
	if CanTransition(from, to) {
		return nil
	}
	return httpx.NewAppError(httpx.CodeConflict, "invalid job status transition", http.StatusConflict, map[string]string{"from": from, "to": to}, nil)
}
