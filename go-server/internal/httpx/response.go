package httpx

import (
	"encoding/json"
	"net/http"
)

const RequestIDHeader = "X-Request-ID"

type APIResponse struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Data      any    `json:"data,omitempty"`
	Details   any    `json:"details,omitempty"`
	RequestID string `json:"request_id"`
}

func WriteJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func WriteOK(w http.ResponseWriter, r *http.Request, data any) {
	WriteJSON(w, http.StatusOK, APIResponse{
		Code:      CodeOK,
		Message:   "success",
		Data:      data,
		RequestID: requestIDFrom(w, r),
	})
}

func WriteError(w http.ResponseWriter, r *http.Request, err error) {
	appErr := ErrorFrom(err)
	WriteJSON(w, appErr.HTTPStatus, APIResponse{
		Code:      appErr.Code,
		Message:   appErr.Message,
		Details:   appErr.Details,
		RequestID: requestIDFrom(w, r),
	})
}

func requestIDFrom(w http.ResponseWriter, r *http.Request) string {
	if value := w.Header().Get(RequestIDHeader); value != "" {
		return value
	}
	if r != nil {
		return r.Header.Get(RequestIDHeader)
	}
	return ""
}
