package jobs

func CanRetry(job Job) bool {
	if job.Attempt >= job.MaxAttempts {
		return false
	}
	switch job.Status {
	case JobStatusFailed, JobStatusCompletedWithFailures:
		return true
	default:
		return false
	}
}
