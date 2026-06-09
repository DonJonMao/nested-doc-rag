package tests

import (
	"bytes"
	"context"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestFileHandlerMultipartUploadSuccess(t *testing.T) {
	workspaceID := uuid.New()
	fileID := uuid.New()
	service := &fakeFileUseCase{uploadResult: &filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "test.png"}}
	router := fileHandlerRouter(filepkg.NewHandler(service))
	body, contentType := multipartBody(t, map[string]string{"workspace_id": workspaceID.String(), "file_category": filepkg.FileCategoryFormTemplate}, "file", "test.png", []byte("content"))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/files", body)
	req.Header.Set("Content-Type", contentType)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.True(t, service.uploadCalled)
	require.Contains(t, rec.Body.String(), fileID.String())
}

func TestFileHandlerMissingWorkspaceRejected(t *testing.T) {
	service := &fakeFileUseCase{}
	router := fileHandlerRouter(filepkg.NewHandler(service))
	body, contentType := multipartBody(t, map[string]string{"file_category": filepkg.FileCategoryFormTemplate}, "file", "test.png", []byte("content"))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/files", body)
	req.Header.Set("Content-Type", contentType)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	require.False(t, service.uploadCalled)
}

func TestFileHandlerMissingFileRejected(t *testing.T) {
	service := &fakeFileUseCase{}
	router := fileHandlerRouter(filepkg.NewHandler(service))
	body, contentType := multipartBody(t, map[string]string{"workspace_id": uuid.NewString(), "file_category": filepkg.FileCategoryFormTemplate}, "", "", nil)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/files", body)
	req.Header.Set("Content-Type", contentType)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusBadRequest, rec.Code)
}

func TestFileHandlerDownloadStreamsContent(t *testing.T) {
	fileID := uuid.New()
	service := &fakeFileUseCase{downloadResult: &filepkg.DownloadResult{Filename: "test.txt", ContentType: "text/plain", ContentLength: 5, Reader: io.NopCloser(strings.NewReader("hello"))}}
	router := fileHandlerRouter(filepkg.NewHandler(service))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/files/"+fileID.String()+"/download", nil)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "hello", rec.Body.String())
	require.Contains(t, rec.Header().Get("Content-Disposition"), "attachment")
}

func TestFileHandlerDeleteReturnsOK(t *testing.T) {
	fileID := uuid.New()
	service := &fakeFileUseCase{}
	router := fileHandlerRouter(filepkg.NewHandler(service))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodDelete, "/api/v1/files/"+fileID.String(), nil)
	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.True(t, service.deleteCalled)
}

type fakeFileUseCase struct {
	uploadResult   *filepkg.File
	downloadResult *filepkg.DownloadResult
	uploadCalled   bool
	deleteCalled   bool
}

func (f *fakeFileUseCase) Upload(ctx context.Context, req filepkg.UploadFileRequest, actor auth.Principal) (*filepkg.File, error) {
	f.uploadCalled = true
	return f.uploadResult, nil
}

func (f *fakeFileUseCase) Get(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*filepkg.File, error) {
	return &filepkg.File{ID: fileID}, nil
}

func (f *fakeFileUseCase) List(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int, actor auth.Principal) ([]filepkg.File, error) {
	return []filepkg.File{{ID: uuid.New(), WorkspaceID: workspaceID}}, nil
}

func (f *fakeFileUseCase) Download(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*filepkg.DownloadResult, error) {
	return f.downloadResult, nil
}

func (f *fakeFileUseCase) Delete(ctx context.Context, fileID uuid.UUID, actor auth.Principal) error {
	f.deleteCalled = true
	return nil
}

func fileHandlerRouter(handler *filepkg.Handler) http.Handler {
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					principal := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
					next.ServeHTTP(w, r.WithContext(auth.ContextWithPrincipal(r.Context(), principal)))
				})
			})
			handler.RegisterRoutes(protected)
		})
	})
	return router
}

func multipartBody(t *testing.T, fields map[string]string, fileField string, filename string, data []byte) (*bytes.Buffer, string) {
	t.Helper()
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	for key, value := range fields {
		require.NoError(t, writer.WriteField(key, value))
	}
	if fileField != "" {
		part, err := writer.CreateFormFile(fileField, filename)
		require.NoError(t, err)
		_, err = part.Write(data)
		require.NoError(t, err)
	}
	require.NoError(t, writer.Close())
	return body, writer.FormDataContentType()
}
