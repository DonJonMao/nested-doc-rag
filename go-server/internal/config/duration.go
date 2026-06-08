package config

import (
	"fmt"
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
