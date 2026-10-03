package jobs

import (
	"testing"
	"time"

	"github.com/hibiken/asynq"
	"github.com/stretchr/testify/require"
)

func TestAsynqQueueZeroTimeoutUsesPracticalNoTimeout(t *testing.T) {
	q := &AsynqQueue{timeout: 0}

	timeout, ok := optionDuration(q.enqueueOptions(Job{}, 0), asynq.TimeoutOpt)

	require.True(t, ok)
	require.Equal(t, asynqPracticalNoTimeout, timeout)
}

func TestAsynqQueuePositiveTimeoutIsPreserved(t *testing.T) {
	q := &AsynqQueue{timeout: 45 * time.Minute}

	timeout, ok := optionDuration(q.enqueueOptions(Job{}, 0), asynq.TimeoutOpt)

	require.True(t, ok)
	require.Equal(t, 45*time.Minute, timeout)
}

func optionDuration(options []asynq.Option, optionType asynq.OptionType) (time.Duration, bool) {
	for _, option := range options {
		if option.Type() == optionType {
			value, ok := option.Value().(time.Duration)
			return value, ok
		}
	}
	return 0, false
}
