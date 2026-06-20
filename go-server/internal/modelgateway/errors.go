package modelgateway

import "net/http"

type GatewayError struct {
	Code       string
	Message    string
	HTTPStatus int
	Cause      error
}

func (e *GatewayError) Error() string {
	if e == nil {
		return ""
	}
	if e.Cause != nil {
		return e.Message + ": " + e.Cause.Error()
	}
	return e.Message
}

func newGatewayError(code string, message string, status int, cause error) *GatewayError {
	if code == "" {
		code = CodeUpstreamFailed
	}
	if message == "" {
		message = "model gateway error"
	}
	if status == 0 {
		status = http.StatusBadGateway
	}
	return &GatewayError{Code: code, Message: message, HTTPStatus: status, Cause: cause}
}
