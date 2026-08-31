package recovery

import (
	"context"
	"errors"
	"time"
)

var ErrUnavailable = errors.New("stream unavailable")

type Stream interface {
	Snapshot(context.Context) ([]byte, error)
	Nudge(context.Context) error
	Open(context.Context) error
}
type Options struct {
	Attempts int
	Delay    time.Duration
	Timeout  time.Duration
}

func (o Options) normalized() Options {
	if o.Attempts < 1 {
		o.Attempts = 5
	}
	if o.Delay < 0 {
		o.Delay = 0
	}
	if o.Timeout <= 0 {
		o.Timeout = 30 * time.Second
	}
	return o
}
func Recover(parent context.Context, s Stream, opts Options) error {
	if err := parent.Err(); err != nil {
		return err
	}
	o := opts.normalized()
	ctx, cancel := context.WithTimeout(parent, o.Timeout)
	defer cancel()
	if _, err := s.Snapshot(ctx); err != nil && !errors.Is(err, ErrUnavailable) {
		return err
	}
	if err := s.Nudge(ctx); err != nil {
		return err
	}
	var last error
	for i := 0; i < o.Attempts; i++ {
		if i > 0 {
			t := time.NewTimer(o.Delay)
			select {
			case <-ctx.Done():
				t.Stop()
				return ctx.Err()
			case <-t.C:
			}
		}
		if _, err := s.Snapshot(ctx); err == nil {
			return s.Open(ctx)
		} else {
			last = err
		}
	}
	if last == nil {
		last = ErrUnavailable
	}
	return last
}
