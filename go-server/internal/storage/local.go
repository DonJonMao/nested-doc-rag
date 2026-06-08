package storage

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

type LocalStorage struct {
	root string
}

func NewLocalStorage(root string) (*LocalStorage, error) {
	if strings.TrimSpace(root) == "" {
		return nil, fmt.Errorf("local storage root is required")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve local storage root: %w", err)
	}
	if err := os.MkdirAll(abs, 0o755); err != nil {
		return nil, fmt.Errorf("create local storage root: %w", err)
	}
	return &LocalStorage{root: abs}, nil
}

func (s *LocalStorage) Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	_ = ctx
	target, cleanKey, err := s.safePath(key)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	tmp := target + ".tmp"
	file, err := os.Create(tmp)
	if err != nil {
		return err
	}
	hasher := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(file, hasher), r)
	closeErr := file.Close()
	if copyErr != nil {
		_ = os.Remove(tmp)
		return copyErr
	}
	if closeErr != nil {
		_ = os.Remove(tmp)
		return closeErr
	}
	if size >= 0 && written != size {
		_ = os.Remove(tmp)
		return fmt.Errorf("object %s size mismatch: expected %d, wrote %d", cleanKey, size, written)
	}
	if err := os.Rename(tmp, target); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	meta := fmt.Sprintf("content-type: %s\netag: %s\n", contentType, hex.EncodeToString(hasher.Sum(nil)))
	return os.WriteFile(target+".meta", []byte(meta), 0o644)
}

func (s *LocalStorage) Get(ctx context.Context, key string) (io.ReadCloser, ObjectInfo, error) {
	_ = ctx
	target, cleanKey, err := s.safePath(key)
	if err != nil {
		return nil, ObjectInfo{}, err
	}
	file, err := os.Open(target)
	if err != nil {
		return nil, ObjectInfo{}, err
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, ObjectInfo{}, err
	}
	objectInfo := ObjectInfo{Key: cleanKey, Size: info.Size(), LastModified: info.ModTime()}
	if meta, err := readLocalMeta(target + ".meta"); err == nil {
		objectInfo.ContentType = meta["content-type"]
		objectInfo.ETag = meta["etag"]
	}
	return file, objectInfo, nil
}

func (s *LocalStorage) Delete(ctx context.Context, key string) error {
	_ = ctx
	target, _, err := s.safePath(key)
	if err != nil {
		return err
	}
	if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.Remove(target + ".meta"); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (s *LocalStorage) PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error) {
	_ = ctx
	_ = ttl
	target, _, err := s.safePath(key)
	if err != nil {
		return "", err
	}
	absolute, err := filepath.Abs(target)
	if err != nil {
		return "", err
	}
	return (&url.URL{Scheme: "file", Path: absolute}).String(), nil
}

func (s *LocalStorage) Health(ctx context.Context) error {
	_ = ctx
	info, err := os.Stat(s.root)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("local storage root is not a directory")
	}
	return nil
}

func (s *LocalStorage) safePath(key string) (string, string, error) {
	cleanKey, err := cleanObjectKey(key)
	if err != nil {
		return "", "", err
	}
	target := filepath.Join(s.root, filepath.FromSlash(cleanKey))
	rel, err := filepath.Rel(s.root, target)
	if err != nil {
		return "", "", err
	}
	if rel == "." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) || rel == ".." || filepath.IsAbs(rel) {
		return "", "", ErrInvalidKey
	}
	return target, cleanKey, nil
}

func cleanObjectKey(key string) (string, error) {
	key = strings.TrimSpace(strings.ReplaceAll(key, "\\", "/"))
	if key == "" || strings.HasPrefix(key, "/") {
		return "", ErrInvalidKey
	}
	for _, part := range strings.Split(key, "/") {
		if part == "" || part == "." || part == ".." {
			return "", ErrInvalidKey
		}
	}
	cleaned := path.Clean(key)
	if cleaned != key || cleaned == "." || strings.HasPrefix(cleaned, "../") {
		return "", ErrInvalidKey
	}
	return cleaned, nil
}

func readLocalMeta(path string) (map[string]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	output := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		name, value, ok := strings.Cut(line, ":")
		if ok {
			output[strings.TrimSpace(name)] = strings.TrimSpace(value)
		}
	}
	return output, nil
}
