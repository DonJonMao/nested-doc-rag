package httpx

import (
	"errors"
	"net/http"
)

const (
	CodeOK                       = "OK"
	CodeInvalidArgument          = "INVALID_ARGUMENT"
	CodeUnauthorized             = "UNAUTHORIZED"
	CodeForbidden                = "FORBIDDEN"
	CodeNotFound                 = "NOT_FOUND"
	CodeConflict                 = "CONFLICT"
	CodeRateLimited              = "RATE_LIMITED"
	CodeInternal                 = "INTERNAL"
	CodePythonRunFailed          = "PYTHON_RUN_FAILED"
	CodeArtifactValidationFailed = "ARTIFACT_VALIDATION_FAILED"
)

type AppError struct {
	Code       string
	Message    string
	Details    any
	HTTPStatus int
	Cause      error
}

func NewAppError(code string, message string, httpStatus int, details any, cause error) *AppError {
	if code == "" {
		code = CodeInternal
	}
	if message == "" {
		message = "internal error"
	}
	if httpStatus == 0 {
		httpStatus = http.StatusInternalServerError
	}
	return &AppError{Code: code, Message: message, Details: details, HTTPStatus: httpStatus, Cause: cause}
}

func (e *AppError) Error() string {
	if e == nil {
		return ""
	}
	if e.Cause != nil {
		return e.Message + ": " + e.Cause.Error()
	}
	return e.Message
}

func (e *AppError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}

func ErrorFrom(err error) *AppError {
	if err == nil {
		return nil
	}
	var appErr *AppError
	if errors.As(err, &appErr) {
		return appErr
	}
	return NewAppError(CodeInternal, "internal server error", http.StatusInternalServerError, nil, err)
}
