package form

const (
	FillRunStatusCreated               = "created"
	FillRunStatusQueued                = "queued"
	FillRunStatusRunning               = "running"
	FillRunStatusSucceeded             = "succeeded"
	FillRunStatusCompletedWithFailures = "completed_with_failures"
	FillRunStatusFailed                = "failed"
	FillRunStatusCanceled              = "canceled"
	FillRunStatusCancelRequested       = "cancel_requested"
)

func ValidFillRunStatus(status string) bool {
	switch status {
	case FillRunStatusCreated, FillRunStatusQueued, FillRunStatusRunning, FillRunStatusSucceeded, FillRunStatusCompletedWithFailures, FillRunStatusFailed, FillRunStatusCanceled, FillRunStatusCancelRequested:
		return true
	default:
		return false
	}
}
