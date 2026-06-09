package artifact

type ManifestArtifact struct {
	Type      string `json:"type"`
	Filename  string `json:"filename"`
	ObjectKey string `json:"object_key,omitempty"`
	LocalPath string `json:"local_path,omitempty"`
	SHA256    string `json:"sha256,omitempty"`
	FileSize  int64  `json:"file_size,omitempty"`
}

type Manifest struct {
	RunID     string             `json:"run_id"`
	Artifacts []ManifestArtifact `json:"artifacts"`
}
