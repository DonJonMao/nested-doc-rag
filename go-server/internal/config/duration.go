package config

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type Duration struct {
	time.Duration
}

func NewDuration(value time.Duration) Duration {
	return Duration{Duration: value}
}

func (d Duration) String() string {
	return d.Duration.String()
}

func (d *Duration) UnmarshalYAML(value *yaml.Node) error {
	if value == nil {
		return nil
	}
	switch value.Kind {
	case yaml.ScalarNode:
		parsed, err := time.ParseDuration(value.Value)
		if err != nil {
			return fmt.Errorf("parse duration %q: %w", value.Value, err)
		}
		d.Duration = parsed
		return nil
	default:
		return fmt.Errorf("duration must be a scalar, got yaml kind %d", value.Kind)
	}
}

type ByteSize struct {
	Bytes int64
}

func NewByteSize(bytes int64) ByteSize {
	return ByteSize{Bytes: bytes}
}

func (s ByteSize) String() string {
	return strconv.FormatInt(s.Bytes, 10)
}

func (s *ByteSize) UnmarshalYAML(value *yaml.Node) error {
	if value == nil {
		return nil
	}
	if value.Kind != yaml.ScalarNode {
		return fmt.Errorf("byte size must be a scalar, got yaml kind %d", value.Kind)
	}
	parsed, err := ParseByteSize(value.Value)
	if err != nil {
		return err
	}
	s.Bytes = parsed
	return nil
}

func ParseByteSize(value string) (int64, error) {
	raw := strings.TrimSpace(value)
	if raw == "" {
		return 0, fmt.Errorf("byte size is empty")
	}
	upper := strings.ToUpper(raw)
	units := []struct {
		suffix string
		mult   int64
	}{
		{"GB", 1024 * 1024 * 1024},
		{"G", 1024 * 1024 * 1024},
		{"MB", 1024 * 1024},
		{"M", 1024 * 1024},
		{"KB", 1024},
		{"K", 1024},
		{"B", 1},
	}
	for _, unit := range units {
		if strings.HasSuffix(upper, unit.suffix) {
			number := strings.TrimSpace(raw[:len(raw)-len(unit.suffix)])
			parsed, err := strconv.ParseInt(number, 10, 64)
			if err != nil {
				return 0, fmt.Errorf("parse byte size %q: %w", value, err)
			}
			return parsed * unit.mult, nil
		}
	}
	parsed, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse byte size %q: %w", value, err)
	}
	return parsed, nil
}
