package sse

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type EventReader interface {
	ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]runevent.RunEvent, error)
}

type Handler struct {
	reader     EventReader
	broker     *Broker
	authorizer WorkspaceAuthorizer
}

func NewHandler(reader EventReader, broker *Broker, authorizer WorkspaceAuthorizer) *Handler {
	return &Handler{reader: reader, broker: broker, authorizer: authorizer}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Get("/runs/{run_id}/events", h.Events)
}

func (h *Handler) Events(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil))
		return
	}
	runID, err := uuid.Parse(chi.URLParam(r, "run_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid run id", http.StatusBadRequest, nil, err))
		return
	}
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.URL.Query().Get("workspace_id")))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err))
		return
	}
	if err := h.authorizer.CanReadWorkspace(r.Context(), workspaceID, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	afterSequence := parseInt64(r.URL.Query().Get("after_sequence"), 0)
	flusher, ok := w.(http.Flusher)
	if !ok {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInternal, "streaming is not supported", http.StatusInternalServerError, nil, nil))
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	history, err := h.reader.ListByRun(r.Context(), workspaceID, runID, afterSequence, 500)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	for _, event := range history {
		if err := writeSSE(w, flusher, Event{RunID: event.RunID, EventType: event.EventType, Sequence: event.Sequence, Payload: event.Payload, CreatedAt: event.CreatedAt}); err != nil {
			return
		}
	}

	events, unsubscribe := h.broker.Subscribe(runID)
	defer unsubscribe()
	ping := time.NewTicker(15 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ping.C:
			_, _ = fmt.Fprint(w, ": ping\n\n")
			flusher.Flush()
		case event, ok := <-events:
			if !ok {
				return
			}
			if err := writeSSE(w, flusher, event); err != nil {
				return
			}
		}
	}
}

func writeSSE(w http.ResponseWriter, flusher http.Flusher, event Event) error {
	data, err := json.Marshal(event)
	if err != nil {
		return err
	}
	if event.Sequence > 0 {
		if _, err := fmt.Fprintf(w, "id: %d\n", event.Sequence); err != nil {
			return err
		}
	}
	if _, err := fmt.Fprintf(w, "event: %s\n", event.EventType); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "data: %s\n\n", data); err != nil {
		return err
	}
	flusher.Flush()
	return nil
}

func parseInt64(value string, fallback int64) int64 {
	parsed, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil || parsed < 0 {
		return fallback
	}
	return parsed
}
