package logging

import (
	"net/url"
	"regexp"
	"strings"
)

const Redacted = "[REDACTED]"

var sensitiveKeyParts = []string{
	"password",
	"token",
	"access_token",
	"refresh_token",
	"authorization",
	"api_key",
	"secret",
	"jwt_secret",
	"minio_secret_key",
	"dsn",
	"database_dsn",
	"deepseek_api_key",
	"openai_api_key",
}

var keyValueSecretPattern = regexp.MustCompile(`(?i)(password|token|api[_-]?key|secret)=([^&\s]+)`)

func RedactSensitiveMap(input map[string]any) map[string]any {
	if input == nil {
		return nil
	}
	out := make(map[string]any, len(input))
	for key, value := range input {
		if IsSensitiveKey(key) {
			out[key] = Redacted
			continue
		}
		switch typed := value.(type) {
		case map[string]any:
			out[key] = RedactSensitiveMap(typed)
		case string:
			out[key] = RedactString(typed)
		default:
			out[key] = value
		}
	}
	return out
}

func RedactString(value string) string {
	if strings.TrimSpace(value) == "" {
		return value
	}
	if parsed, err := url.Parse(value); err == nil && parsed.User != nil {
		if _, hasPassword := parsed.User.Password(); hasPassword {
			parsed.User = url.UserPassword(parsed.User.Username(), Redacted)
			value = parsed.String()
			value = strings.ReplaceAll(value, "%5BREDACTED%5D", Redacted)
		}
	}
	return keyValueSecretPattern.ReplaceAllString(value, "$1="+Redacted)
}

func IsSensitiveKey(key string) bool {
	lower := strings.ToLower(strings.TrimSpace(key))
	for _, part := range sensitiveKeyParts {
		if strings.Contains(lower, part) {
			return true
		}
	}
	return false
}
