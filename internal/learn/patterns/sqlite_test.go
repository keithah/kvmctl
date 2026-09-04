// Copyright 2026 keithah and contributors. Licensed under Apache-2.0. See LICENSE.

package patterns

import (
	"database/sql"
	"os"
	"testing"

	_ "modernc.org/sqlite"
)

// TestMain initializes modernc SQLite before parallel tests open databases.
func TestMain(m *testing.M) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		panic(err)
	}
	if err := db.Ping(); err != nil {
		panic(err)
	}
	_ = db.Close()
	os.Exit(m.Run())
}
