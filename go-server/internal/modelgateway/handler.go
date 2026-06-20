package modelgateway

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type Handler struct {
	service *Service
}

func NewHandler(service *Service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	if h == nil || h.service == nil || !h.service.Enabled() {
		return
	}
	r.Route("/internal/model-gateway", func(internal chi.Router) {
		internal.Use(h.internalAuth)
		internal.Post("/v1/chat/completions", h.proxy(KindChat))
		internal.Post("/v1/embeddings", h.proxy(KindEmbedding))
		internal.Post("/v1/rerank", h.proxy(KindRerank))
		internal.Get("/stats", h.stats)
	})
}

func (h *Handler) internalAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := h.service.Authorize(r); err != nil {
			writeGatewayError(w, metadataFromRequest(r, ""), err)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (h *Handler) proxy(kind string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		metadata := metadataFromRequest(r, kind)
		w.Header().Set(HeaderRequestID, metadata.RequestID)
		if r.Method != http.MethodPost {
			writeGatewayError(w, metadata, newGatewayError(CodeInvalidRequest, "method not allowed", http.StatusMethodNotAllowed, nil))
			return
		}
		contentType := strings.ToLower(r.Header.Get("Content-Type"))
		if !strings.Contains(contentType, "application/json") {
			writeGatewayError(w, metadata, newGatewayError(CodeInvalidRequest, "content-type must be application/json", http.StatusBadRequest, nil))
			return
		}
		limit := h.service.BodyLimit()
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
		if err != nil {
			writeGatewayError(w, metadata, newGatewayError(CodeBodyTooLarge, "request body is too large", http.StatusRequestEntityTooLarge, err))
			return
		}
		if !json.Valid(body) {
			writeGatewayError(w, metadata, newGatewayError(CodeInvalidRequest, "request body must be valid JSON", http.StatusBadRequest, nil))
			return
		}
		if kind == KindChat && hasStreamTrue(body) {
			writeGatewayError(w, metadata, newGatewayError(CodeStreamNotSupported, "streaming chat requests are not supported by model gateway", http.StatusBadRequest, nil))
			return
		}
		result, gatewayErr := h.service.Proxy(r.Context(), kind, metadata, body)
		if gatewayErr != nil {
			writeGatewayError(w, metadata, gatewayErr)
			return
		}
		contentType = result.Header["Content-Type"]
		if contentType == "" {
			contentType = "application/json; charset=utf-8"
		}
		w.Header().Set("Content-Type", contentType)
		w.Header().Set(HeaderRequestID, metadata.RequestID)
		status := result.StatusCode
		if status == 0 {
			status = http.StatusOK
		}
		w.WriteHeader(status)
		_, _ = w.Write(result.Body)
	}
}

func (h *Handler) stats(w http.ResponseWriter, r *http.Request) {
	metadata := metadataFromRequest(r, "")
	w.Header().Set(HeaderRequestID, metadata.RequestID)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(h.service.Stats())
}

func metadataFromRequest(r *http.Request, kind string) Metadata {
	requestID := strings.TrimSpace(r.Header.Get(HeaderRequestID))
	if requestID == "" {
		requestID = uuid.NewString()
	}
	headerKind := strings.TrimSpace(r.Header.Get(HeaderModelKind))
	if kind == "" {
		kind = headerKind
	}
	return Metadata{
		RequestID:   requestID,
		RunID:       strings.TrimSpace(r.Header.Get(HeaderRunID)),
		FieldID:     strings.TrimSpace(r.Header.Get(HeaderFieldID)),
		JobID:       strings.TrimSpace(r.Header.Get(HeaderJobID)),
		UserID:      strings.TrimSpace(r.Header.Get(HeaderUserID)),
		WorkspaceID: strings.TrimSpace(r.Header.Get(HeaderWorkspaceID)),
		ModelKind:   kind,
		Purpose:     strings.TrimSpace(r.Header.Get(HeaderModelPurpose)),
	}
}

func hasStreamTrue(body []byte) bool {
	var value map[string]json.RawMessage
	if err := json.Unmarshal(body, &value); err != nil {
		return false
	}
	raw, ok := value["stream"]
	if !ok {
		return false
	}
	var stream bool
	return json.Unmarshal(raw, &stream) == nil && stream
}

func writeGatewayError(w http.ResponseWriter, metadata Metadata, err *GatewayError) {
	if err == nil {
		err = newGatewayError(CodeUpstreamFailed, "model gateway error", http.StatusBadGateway, nil)
	}
	if metadata.RequestID == "" {
		metadata.RequestID = uuid.NewString()
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set(HeaderRequestID, metadata.RequestID)
	w.WriteHeader(err.HTTPStatus)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": map[string]any{
			"code":       err.Code,
			"message":    err.Message,
			"request_id": metadata.RequestID,
			"model_kind": metadata.ModelKind,
		},
	})
}
