package host

import (
	"context"
	"fmt"
)

func Reboot(ctx context.Context, r Runner, p Profile, target string, yes bool) (map[string]any, error) {
	if !yes {
		return nil, fmt.Errorf("host.reboot requires explicit --yes")
	}
	id, err := Probe(ctx, r, p)
	if err != nil {
		return nil, err
	}
	if id["hostname"] != target {
		return nil, fmt.Errorf("host identity mismatch")
	}
	if _, err = r.Run(ctx, []string{"systemctl", "reboot", "--yes"}, p.Timeout); err != nil {
		return nil, err
	}
	return map[string]any{"operation": "host.reboot", "target": target, "requested": true}, nil
}
