package recovery

import (
	"context"
	"errors"
	"testing"
)

type fake struct {
	n      int
	nudges int
	opened int
}

func (f *fake) Snapshot(ctx context.Context) ([]byte, error) {
	f.n++
	if f.n == 1 {
		return nil, ErrUnavailable
	}
	return []byte("frame"), nil
}
func (f *fake) Nudge(ctx context.Context) error { f.nudges++; return nil }
func (f *fake) Open(ctx context.Context) error  { f.opened++; return nil }
func TestRecoverToleratesUnavailableAndOpensStream(t *testing.T) {
	f := &fake{}
	if err := Recover(context.Background(), f, Options{Attempts: 3}); err != nil {
		t.Fatal(err)
	}
	if f.nudges != 1 || f.opened != 1 {
		t.Fatalf("nudge=%d open=%d", f.nudges, f.opened)
	}
}
func TestRecoverHonorsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	f := &fake{}
	if err := Recover(ctx, f, Options{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("err=%v", err)
	}
}
