package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestReviewHandlerListAndGet(t *testing.T) {
	runID := uuid.New()
	itemID := uuid.New()
	reviews := &fakeReviewUseCase{
		items:  []reviewpkg.ReviewItem{{ID: itemID, RunID: runID, FieldID: "f1"}},
		item:   &reviewpkg.ReviewItem{ID: itemID, RunID: runID, FieldID: "f1"},
		counts: reviewpkg.ReviewCounts{Total: 1, Pending: 1},
	}
	handler := reviewpkg.NewHandler(reviews, &fakeReviewResultRuns{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	ctx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})

	listRec := httptest.NewRecorder()
	router.ServeHTTP(listRec, httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String()+"/review-items?status=pending", nil).WithContext(ctx))
	require.Equal(t, http.StatusOK, listRec.Code)
	require.Contains(t, listRec.Body.String(), `"counts"`)

	getRec := httptest.NewRecorder()
	router.ServeHTTP(getRec, httptest.NewRequest(http.MethodGet, "/review-items/"+itemID.String(), nil).WithContext(ctx))
	require.Equal(t, http.StatusOK, getRec.Code)
	require.Contains(t, getRec.Body.String(), `"field_id":"f1"`)
}

func TestReviewHandlerActions(t *testing.T) {
	itemID := uuid.New()
	reviews := &fakeReviewUseCase{item: &reviewpkg.ReviewItem{ID: itemID, Status: reviewpkg.ReviewStatusApproved}}
	handler := reviewpkg.NewHandler(reviews, &fakeReviewResultRuns{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	ctx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})
	cases := []struct {
		path string
		body string
		want string
	}{
		{"/review-items/" + itemID.String() + "/approve", `{"comment":"ok"}`, reviewpkg.ReviewActionApprove},
		{"/review-items/" + itemID.String() + "/reject", `{"reason":"证据不足"}`, reviewpkg.ReviewActionReject},
		{"/review-items/" + itemID.String() + "/edit", `{"edited_answer":"人工答案","comment":"现场确认"}`, reviewpkg.ReviewActionEdit},
		{"/review-items/" + itemID.String() + "/ignore", `{"comment":"ignore"}`, reviewpkg.ReviewActionIgnore},
		{"/review-items/" + itemID.String() + "/reopen", `{"comment":"again"}`, reviewpkg.ReviewActionReopen},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(tc.body)).WithContext(ctx))
		require.Equal(t, http.StatusOK, rec.Code, tc.path)
		require.Equal(t, tc.want, reviews.actions[len(reviews.actions)-1])
	}
}

func TestReviewHandlerExportJSONAndCSV(t *testing.T) {
	runID := uuid.New()
	reviews := &fakeReviewUseCase{items: []reviewpkg.ReviewItem{{ID: uuid.New(), RunID: runID, QuestionText: "机房,名称", AnswerValue: "西咸4号楼"}}}
	handler := reviewpkg.NewHandler(reviews, &fakeReviewResultRuns{})
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	ctx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})

	jsonRec := httptest.NewRecorder()
	router.ServeHTTP(jsonRec, httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String()+"/review-items/export?format=json", nil).WithContext(ctx))
	require.Equal(t, http.StatusOK, jsonRec.Code)
	require.Contains(t, jsonRec.Header().Get("Content-Disposition"), "review_items.json")

	csvRec := httptest.NewRecorder()
	router.ServeHTTP(csvRec, httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String()+"/review-items/export?format=csv", nil).WithContext(ctx))
	require.Equal(t, http.StatusOK, csvRec.Code)
	require.Contains(t, csvRec.Header().Get("Content-Disposition"), "review_items.csv")
	require.Contains(t, csvRec.Body.String(), "西咸4号楼")
}

func TestReviewHandlerResultCenter(t *testing.T) {
	runID := uuid.New()
	run := &formpkg.FillRun{ID: runID, WorkspaceID: uuid.New(), Status: formpkg.FillRunStatusSucceeded}
	reviews := &fakeReviewUseCase{counts: reviewpkg.ReviewCounts{Total: 2, Pending: 1}}
	runs := &fakeReviewResultRuns{
		run:       run,
		artifacts: []artifact.RunArtifact{{ID: uuid.New(), RunID: runID, ArtifactType: artifact.TypeFilledForm}},
	}
	handler := reviewpkg.NewHandler(reviews, runs)
	router := chi.NewRouter()
	handler.RegisterRoutes(router)
	ctx := auth.ContextWithPrincipal(context.Background(), auth.Principal{UserID: uuid.New()})

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/fill-runs/"+runID.String()+"/result", nil).WithContext(ctx))

	require.Equal(t, http.StatusOK, rec.Code)
	require.Contains(t, rec.Body.String(), `"review_counts"`)
	require.Contains(t, rec.Body.String(), "/download/filled-form")
}
