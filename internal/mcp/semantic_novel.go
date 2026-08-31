package mcp

import (
	"context"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
	"kvmctl-pp-cli/internal/semantic"
	"os"
	"strings"
)

func registerSemanticTool(s *server.MCPServer) {
	tool := mcp.NewTool("semantic_dispatch", mcp.WithDescription("Dispatch one structured semantic KVM operation: status, snapshot, keyboard, mouse, target-switch, or sequence."), mcp.WithString("operation", mcp.Required()), mcp.WithObject("arguments"))
	s.AddTool(tool, func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		c, session, err := newMCPClient(ctx)
		if err != nil {
			return mcpToolError(err.Error()), nil
		}
		if session != nil {
			defer session.ZeroCredentials()
		}
		args := req.GetArguments()
		raw, _ := args["arguments"].(map[string]any)
		if raw == nil {
			raw = map[string]any{}
		}
		raw["write_enabled"] = envTruthy(os.Getenv("KVMCTL_WRITE_ENABLED"))
		out, err := semantic.Dispatch(ctx, c, stringArg(args, "operation"), raw)
		if err != nil {
			return mcpToolError(err.Error()), nil
		}
		return mcp.NewToolResultText(string(out)), nil
	})
}
func stringArg(m map[string]any, k string) string { v, _ := m[k].(string); return v }
func envTruthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}
