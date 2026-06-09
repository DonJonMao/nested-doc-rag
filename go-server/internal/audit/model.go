package audit

import (
	"time"

	"github.com/google/uuid"
)

type AuditLog struct {
	ID           uuid.UUID
	WorkspaceID  *uuid.UUID
	UserID       *uuid.UUID
	Action       string
	ResourceType string
	ResourceID   string
	IP           string
	UserAgent    string
	Payload      map[string]any
	CreatedAt    time.Time
}
