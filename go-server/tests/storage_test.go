package tests

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/stretchr/testify/require"
)

func TestLocalStoragePutGetDelete(t *testing.T) {
	ctx := context.Background()
	store, err := storage.NewLocalStorage(t.TempDir())
	require.NoError(t, err)

	err = store.Put(ctx, "workspaces/ws/files/test.txt", strings.NewReader("hello"), 5, "text/plain")
	require.NoError(t, err)

	reader, info, err := store.Get(ctx, "workspaces/ws/files/test.txt")
	require.NoError(t, err)
	defer reader.Close()
	data, err := io.ReadAll(reader)
	require.NoError(t, err)
	require.Equal(t, "hello", string(data))
	require.Equal(t, int64(5), info.Size)
	require.Equal(t, "text/plain", info.ContentType)
	require.NotEmpty(t, info.ETag)

	u, err := store.PresignGet(ctx, "workspaces/ws/files/test.txt", time.Minute)
	require.NoError(t, err)
	require.True(t, strings.HasPrefix(u, "file://"))

	require.NoError(t, store.Delete(ctx, "workspaces/ws/files/test.txt"))
	_, _, err = store.Get(ctx, "workspaces/ws/files/test.txt")
	require.Error(t, err)
}

func TestLocalStorageRejectsPathTraversal(t *testing.T) {
	ctx := context.Background()
	store, err := storage.NewLocalStorage(t.TempDir())
	require.NoError(t, err)

	for _, key := range []string{"../secret.txt", "a/../secret.txt", "/absolute/path", "a//b"} {
		err := store.Put(ctx, key, strings.NewReader("bad"), 3, "text/plain")
		require.Error(t, err, key)
		require.True(t, errors.Is(err, storage.ErrInvalidKey), key)
	}
}
