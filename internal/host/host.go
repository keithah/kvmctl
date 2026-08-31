// Package host provides bounded, allowlisted remote probes.
package host

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"time"
)

type Result struct {
	Code   int
	Stdout string
}
type Runner interface {
	Run(context.Context, []string, time.Duration) (Result, error)
}
type Profile struct {
	Service string
	DRMNode string
	Timeout time.Duration
}

var nameRE = regexp.MustCompile(`^[A-Za-z0-9_.@-]+$`)
var nodeRE = regexp.MustCompile(`^/dev/dri/(card|renderD|controlD)[0-9]+$`)
var secretRE = regexp.MustCompile(`(?i)(password|passwd|token|secret|private[_ -]?key)\s*[:=]`)

func Probe(ctx context.Context, r Runner, p Profile) (map[string]any, error) {
	if p.Service == "" {
		p.Service = "kvm-render"
	}
	if p.DRMNode == "" {
		p.DRMNode = "/dev/dri/renderD128"
	}
	if p.Timeout <= 0 {
		p.Timeout = 10 * time.Second
	}
	if !nameRE.MatchString(p.Service) || !nodeRE.MatchString(p.DRMNode) {
		return nil, fmt.Errorf("invalid probe profile")
	}
	run := func(argv []string) (Result, error) {
		res, err := r.Run(ctx, argv, p.Timeout)
		if err != nil {
			return Result{}, err
		}
		if len(res.Stdout) > 65536 || secretRE.MatchString(res.Stdout) {
			return Result{}, fmt.Errorf("unsafe probe output")
		}
		return res, nil
	}
	h, e := run([]string{"hostname"})
	if e != nil {
		return nil, e
	}
	hostname := strings.TrimSpace(h.Stdout)
	if !nameRE.MatchString(hostname) {
		return nil, fmt.Errorf("malformed hostname")
	}
	os, e := run([]string{"cat", "/etc/os-release"})
	if e != nil {
		return nil, e
	}
	fields := map[string]string{}
	for _, line := range strings.Split(os.Stdout, "\n") {
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 || !regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`).MatchString(parts[0]) {
			return nil, fmt.Errorf("malformed os-release")
		}
		fields[parts[0]] = strings.Trim(strings.TrimSpace(parts[1]), `"`)
	}
	for _, k := range []string{"NAME", "VERSION_ID", "PRETTY_NAME"} {
		if fields[k] == "" {
			return nil, fmt.Errorf("missing os-release field %s", k)
		}
	}
	return map[string]any{"probe": "host.identity.inspect", "hostname": hostname, "os": map[string]string{"name": fields["NAME"], "version_id": fields["VERSION_ID"], "pretty_name": fields["PRETTY_NAME"]}}, nil
}
