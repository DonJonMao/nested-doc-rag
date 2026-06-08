package tests

import (
	"errors"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/stretchr/testify/require"
)

func TestAppErrorCodeAndStatus(t *testing.T) {
	err := httpx.NewAppError(httpx.CodeNotFound, "not found", http.StatusNotFound, nil, nil)

	require.Equal(t, httpx.CodeNotFound, err.Code)
	require.Equal(t, http.StatusNotFound, err.HTTPStatus)
	require.Equal(t, "not found", err.Error())
}

func TestUnknownErrorMapsToInternal(t *testing.T) {
	appErr := httpx.ErrorFrom(errors.New("boom"))

	require.Equal(t, httpx.CodeInternal, appErr.Code)
	require.Equal(t, http.StatusInternalServerError, appErr.HTTPStatus)
	require.Equal(t, "internal server error", appErr.Message)
}
