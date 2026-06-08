package logging

import (
	"fmt"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

func New(cfg config.LoggingConfig) (*zap.Logger, error) {
	var zapCfg zap.Config
	if cfg.Development {
		zapCfg = zap.NewDevelopmentConfig()
	} else {
		zapCfg = zap.NewProductionConfig()
	}
	if cfg.Encoding != "" {
		zapCfg.Encoding = cfg.Encoding
	}
	if cfg.Level != "" {
		var level zapcore.Level
		if err := level.Set(cfg.Level); err != nil {
			return nil, fmt.Errorf("invalid logging.level %q: %w", cfg.Level, err)
		}
		zapCfg.Level = zap.NewAtomicLevelAt(level)
	}
	zapCfg.DisableStacktrace = !cfg.Development
	return zapCfg.Build()
}

func WithRequestID(logger *zap.Logger, requestID string) *zap.Logger {
	if logger == nil {
		return zap.NewNop()
	}
	if requestID == "" {
		return logger
	}
	return logger.With(zap.String("request_id", requestID))
}
