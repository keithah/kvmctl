package client

import (
	"context"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/gorilla/websocket"
	"github.com/mvanhorn/printing-press-library/library/devices/kvmctl/internal/config"
)

func TestKVMDLoginFormAndCapabilities(t *testing.T) {
	var gotToken bool
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/auth/login" {
			if err := r.ParseForm(); err != nil {
				t.Fatal(err)
			}
			if r.Form.Get("passwd") != "p&x" {
				t.Fatalf("password not form encoded")
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"ok":true,"result":{"token":"tok"}}`))
			return
		}
		if r.URL.Path == "/api/info" {
			gotToken = r.Header.Get("token") == "tok"
			w.Write([]byte(`{"ok":true,"result":{"hid":{"enabled":true,"connected":true},"streamer":{},"extras":{"ocr":{"enabled":true,"languages":{"eng":{}}},"switch":{"enabled":true}}}}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer s.Close()
	c := New(&config.Config{BaseURL: s.URL}, 0, 0)
	tok, err := c.KVMDLogin(context.Background(), "u", "p&x")
	if err != nil {
		t.Fatal(err)
	}
	if tok != "tok" {
		t.Fatalf("token=%q", tok)
	}
	c.Config.KvmctlKvmdToken = tok
	caps, err := c.KVMDCapabilities(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !gotToken || !caps["hid"] || !caps["stream"] || !caps["ocr"] || !caps["switch"] {
		t.Fatalf("token=%v caps=%v", gotToken, caps)
	}
}

func TestKVMDSnapshotRetriesUnderAuthenticatedStreamLease(t *testing.T) {
	var streamOpen atomic.Bool
	var snapshotRequests atomic.Int32
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/ws":
			if r.URL.Query().Get("stream") != "1" || r.Header.Get("token") != "tok" {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			conn, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				t.Errorf("upgrade: %v", err)
				return
			}
			streamOpen.Store(true)
			defer conn.Close()
			_, _, _ = conn.ReadMessage()
		case "/api/streamer/snapshot":
			request := snapshotRequests.Add(1)
			if !streamOpen.Load() || request < 5 {
				http.Error(w, "stream warming", http.StatusServiceUnavailable)
				return
			}
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("fresh-snapshot"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer s.Close()

	c := New(&config.Config{BaseURL: s.URL, KvmctlKvmdToken: "tok", KVMCTLInsecure: true}, 0, 0)
	got, err := c.KVMDSnapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "fresh-snapshot" || snapshotRequests.Load() < 2 || !streamOpen.Load() {
		t.Fatalf("snapshot=%q requests=%d streamOpen=%v", got, snapshotRequests.Load(), streamOpen.Load())
	}
}

func TestOpenKVMDStreamUsesConfiguredCustomCA(t *testing.T) {
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/ws" || r.Header.Get("token") != "tok" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
	}))
	defer s.Close()

	caPath := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(caPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	c := New(&config.Config{BaseURL: s.URL, KvmctlKvmdToken: "tok", KVMCTLCA: caPath}, 0, 0)
	lease, err := c.OpenKVMDStream(context.Background())
	if err != nil {
		t.Fatalf("custom CA websocket lease failed: %v", err)
	}
	_ = lease.Close()
}
func TestKVMDValidation(t *testing.T) {
	c := &Client{}
	if err := c.KVMDMouseMove(context.Background(), 32768, 0); err == nil {
		t.Fatal("expected coordinate validation")
	}
	if err := c.KVMDMouseWheel(context.Background(), 0, 128); err == nil {
		t.Fatal("expected wheel validation")
	}
	if err := c.KVMDMouseButton(context.Background(), "bogus", true); err == nil {
		t.Fatal("expected button validation")
	}
	if err := c.KVMDShortcut(context.Background(), "ControlLeft,,Enter"); err == nil {
		t.Fatal("expected shortcut validation")
	}
}
