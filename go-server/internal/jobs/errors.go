package jobs

import "errors"

var (
	ErrHandlerNotImplemented = errors.New("handler not implemented in Block 3")
	ErrJobCanceled           = errors.New("job canceled")
)
