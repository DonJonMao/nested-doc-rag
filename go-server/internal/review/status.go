package review

const (
	ReviewStatusPending  = "pending"
	ReviewStatusApproved = "approved"
	ReviewStatusRejected = "rejected"
	ReviewStatusEdited   = "edited"
	ReviewStatusIgnored  = "ignored"
	ReviewStatusReopened = "reopened"
)

const (
	ReviewRiskLow    = "low"
	ReviewRiskMedium = "medium"
	ReviewRiskHigh   = "high"
)

const (
	ReviewActionApprove = "approve"
	ReviewActionReject  = "reject"
	ReviewActionEdit    = "edit"
	ReviewActionIgnore  = "ignore"
	ReviewActionReopen  = "reopen"
)

func ValidReviewStatus(status string) bool {
	switch status {
	case ReviewStatusPending, ReviewStatusApproved, ReviewStatusRejected, ReviewStatusEdited, ReviewStatusIgnored, ReviewStatusReopened:
		return true
	default:
		return false
	}
}

func ValidRiskLevel(risk string) bool {
	switch risk {
	case ReviewRiskLow, ReviewRiskMedium, ReviewRiskHigh:
		return true
	default:
		return false
	}
}
