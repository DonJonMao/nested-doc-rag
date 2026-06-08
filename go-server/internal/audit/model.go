package audit

import "time"

type Event struct {
	ID          string
	ActorID     string
	WorkspaceID string
	Action      string
	Resource    string
	ResourceID  string
	Metadata    map[string]any
	CreatedAt   time.Time
}
