package auth

import (
	"testing"
)

func TestHashKey(t *testing.T) {
	t.Run("deterministic", func(t *testing.T) {
		if hashKey("mk_abc123") != hashKey("mk_abc123") {
			t.Error("same key must produce same hash")
		}
	})

	t.Run("distinct keys produce distinct hashes", func(t *testing.T) {
		if hashKey("mk_abc123") == hashKey("mk_abc124") {
			t.Error("different keys produced the same hash")
		}
	})

	t.Run("hash does not contain the raw key", func(t *testing.T) {
		h := hashKey("mk_secret_value")
		if h == "mk_secret_value" {
			t.Error("hash must not equal the raw key")
		}
		if len(h) != 64 { // SHA256 hex
			t.Errorf("expected 64-char SHA256 hex digest, got %d chars", len(h))
		}
	})
}

func TestGenerateKey(t *testing.T) {
	k1, err := generateKey()
	if err != nil {
		t.Fatal(err)
	}
	k2, _ := generateKey()

	if k1 == k2 {
		t.Error("generateKey produced a duplicate")
	}
	if len(k1) != 3+64 { // "mk_" + 32 bytes hex
		t.Errorf("unexpected key length %d", len(k1))
	}
	if k1[:3] != "mk_" {
		t.Errorf("key missing mk_ prefix: %s", k1[:8])
	}
}
