// PATCH(library): expose target-bound sequence validation through novel hook.
package cli

import (
	"fmt"

	"github.com/spf13/cobra"
	"kvmctl-pp-cli/internal/sequence"
)

// pp:data-source local
func init() { registerNovelCommand(registerSequenceCommands) }

func registerSequenceCommands(root *cobra.Command, flags *rootFlags) {
	parent := &cobra.Command{Use: "sequence", Short: "Validate target-bound KVM sequences", Annotations: map[string]string{"pp:novel": "true"}}
	var file string
	validate := &cobra.Command{Use: "validate", Short: "Validate a sequence document without executing it", Annotations: map[string]string{"mcp:read-only": "true", "pp:novel": "true"}, RunE: func(cmd *cobra.Command, args []string) error {
		if file == "" {
			return usageErr(fmt.Errorf("--file is required"))
		}
		p, err := sequence.ReadDocument(file)
		if err != nil {
			return err
		}
		h, err := p.Hash()
		if err != nil {
			return err
		}
		return kvmdJSON(flags, cmd, map[string]any{"valid": true, "target": p.Target, "actions": len(p.Actions), "plan_hash": h})
	}}
	validate.Flags().StringVar(&file, "file", "", "JSON sequence document")
	parent.AddCommand(validate)
	addNovelCommandIfAbsent(root, parent)
}
