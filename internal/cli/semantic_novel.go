// PATCH(library): expose the shared semantic dispatcher through the CLI hook.
package cli

import (
	"encoding/json"
	"fmt"
	"github.com/spf13/cobra"
	"kvmctl-pp-cli/internal/semantic"
)

func init() { registerNovelCommand(registerSemanticCommands) }

func registerSemanticCommands(root *cobra.Command, flags *rootFlags) {
	parent := &cobra.Command{Use: "semantic", Short: "Structured semantic KVM operations", Annotations: map[string]string{"pp:novel": "true"}}
	for _, name := range semantic.Operations {
		n := name
		cmd := &cobra.Command{Use: n, Short: "Execute semantic " + n + " operation", Annotations: map[string]string{"pp:novel": "true"}, RunE: func(cmd *cobra.Command, _ []string) error {
			if n != "status" && n != "snapshot" && !flags.yes {
				return usageErr(fmt.Errorf("--yes is required for semantic %s", n))
			}
			c, err := flags.newClient()
			if err != nil {
				return err
			}
			args := map[string]any{"write_enabled": flags.yes}
			if n == "keyboard" {
				args["key"], _ = cmd.Flags().GetString("key")
			}
			if n == "mouse" {
				args["x"], _ = cmd.Flags().GetInt("x")
				args["y"], _ = cmd.Flags().GetInt("y")
			}
			if n == "target-switch" {
				args["target"], _ = cmd.Flags().GetString("target")
			}
			if n == "sequence" {
				raw, _ := cmd.Flags().GetString("actions")
				if raw != "" {
					var actions []any
					if err := json.Unmarshal([]byte(raw), &actions); err != nil {
						return err
					}
					args["actions"] = actions
				}
			}
			out, err := semantic.Dispatch(cmd.Context(), c, n, args)
			if err != nil {
				return err
			}
			return flags.printJSON(cmd, json.RawMessage(out))
		}}
		switch n {
		case "keyboard":
			cmd.Flags().String("key", "", "key name")
		case "mouse":
			cmd.Flags().Int("x", 0, "normalized x")
			cmd.Flags().Int("y", 0, "normalized y")
		case "target-switch":
			cmd.Flags().String("target", "", "target port")
		case "sequence":
			cmd.Flags().String("actions", "", "JSON action array")
		}
		parent.AddCommand(cmd)
	}
	addNovelCommandIfAbsent(root, parent)
}
