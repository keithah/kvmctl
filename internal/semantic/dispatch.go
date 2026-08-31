// Package semantic is the single structured operation path shared by front ends.
package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"kvmctl-pp-cli/internal/client"
	"kvmctl-pp-cli/internal/results"
)

var Operations = []string{"status", "snapshot", "keyboard", "mouse", "target-switch", "sequence"}

func Dispatch(ctx context.Context, c *client.Client, name string, args map[string]any) (json.RawMessage, error) {
	if c == nil {
		return nil, fmt.Errorf("client is required")
	}
	if !known(name) {
		return nil, fmt.Errorf("unknown operation %q", name)
	}
	if args == nil {
		args = map[string]any{}
	}
	write := boolArg(args, "write_enabled")
	if name != "status" && name != "snapshot" && !write {
		return nil, fmt.Errorf("operation %s requires write_enabled", name)
	}
	var out results.Operation
	switch name {
	case "status":
		data, err := c.Get(ctx, "/api/info", nil)
		if err != nil {
			return nil, err
		}
		out = results.Build(name, "kvm", true, "", true, false, "observed", map[string]any{"info": json.RawMessage(data)}, nil)
	case "snapshot":
		data, err := c.GetWithHeaders(ctx, "/api/streamer/snapshot", map[string]string{"preview": "true"}, map[string]string{"Accept": "image/jpeg", client.BinaryResponseHeader: "true"})
		if err != nil {
			return nil, err
		}
		out = results.Build(name, "kvm", true, "", true, false, "observed", map[string]any{"data": json.RawMessage(data)}, nil)
	case "keyboard":
		key, ok := args["key"].(string)
		if !ok || key == "" {
			return nil, fmt.Errorf("key is required")
		}
		if err := c.KVMDKey(ctx, key, true); err != nil {
			return nil, err
		}
		if err := c.KVMDKey(ctx, key, false); err != nil {
			return nil, err
		}
		out = results.Build(name, "kvm", false, "", true, true, "completed", map[string]any{"key": key}, nil)
	case "mouse":
		x, xok := intArg(args, "x")
		y, yok := intArg(args, "y")
		if !xok || !yok {
			return nil, fmt.Errorf("x and y are required")
		}
		if err := c.KVMDMouseMove(ctx, x, y); err != nil {
			return nil, err
		}
		out = results.Build(name, "kvm", false, "", true, true, "completed", map[string]any{"x": x, "y": y}, nil)
	case "target-switch":
		target, ok := args["target"].(string)
		if !ok || target == "" {
			return nil, fmt.Errorf("target is required")
		}
		_, _, err := c.PostWithParams(ctx, "/api/switch/port", map[string]string{"port": target}, map[string]any{})
		if err != nil {
			return nil, err
		}
		out = results.Build(name, "kvm", false, target, true, true, "completed", map[string]any{"target": target}, nil)
	case "sequence":
		actions, ok := args["actions"].([]any)
		if !ok || len(actions) > 32 {
			return nil, fmt.Errorf("actions must be an array of at most 32 items")
		}
		for _, raw := range actions {
			a, ok := raw.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("invalid sequence action")
			}
			kind, _ := a["operation"].(string)
			if kind == "" {
				kind, _ = a["action"].(string)
			}
			if kind != "keyboard" && kind != "mouse" {
				return nil, fmt.Errorf("unsupported sequence action")
			}
			a["write_enabled"] = true
			if _, err := Dispatch(ctx, c, kind, a); err != nil {
				return nil, err
			}
		}
		out = results.Build(name, "kvm", false, "", true, len(actions) > 0, "completed", map[string]any{"action_count": len(actions)}, nil)
	}
	data, err := json.Marshal(out)
	return data, err
}

func known(s string) bool {
	for _, n := range Operations {
		if s == n {
			return true
		}
	}
	return false
}
func intArg(m map[string]any, k string) (int, bool) {
	switch v := m[k].(type) {
	case int:
		return v, true
	case float64:
		return int(v), true
	default:
		return 0, false
	}
}
func boolArg(m map[string]any, k string) bool { v, _ := m[k].(bool); return v }

var _ = Operations
