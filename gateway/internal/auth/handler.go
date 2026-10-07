package auth

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// AdminHandler handles JWT-protected key management endpoints.
type AdminHandler struct {
	db        *pgxpool.Pool
	jwtSecret []byte
}

func NewAdminHandler(db *pgxpool.Pool, jwtSecret string) *AdminHandler {
	return &AdminHandler{db: db, jwtSecret: []byte(jwtSecret)}
}

// JWTMiddleware validates the Authorization: Bearer <token> header.
func (h *AdminHandler) JWTMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		tokenStr := c.GetHeader("Authorization")
		if len(tokenStr) > 7 && tokenStr[:7] == "Bearer " {
			tokenStr = tokenStr[7:]
		}

		token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, fmt.Errorf("unexpected signing method: %v", t.Header["alg"])
			}
			return h.jwtSecret, nil
		})
		if err != nil || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid admin token"})
			return
		}

		claims, ok := token.Claims.(jwt.MapClaims)
		if !ok {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid claims"})
			return
		}
		c.Set("admin_tenant_id", claims["tenant_id"])
		c.Next()
	}
}

type createKeyRequest struct {
	Name      string `json:"name" binding:"required"`
	TenantID  string `json:"tenant_id" binding:"required"`
	ExpiresAt string `json:"expires_at,omitempty"` // RFC3339 or empty
}

// CreateKey handles POST /admin/keys.
func (h *AdminHandler) CreateKey(c *gin.Context) {
	var req createKeyRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	rawKey, err := generateKey()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate key"})
		return
	}

	hash := hashKey(rawKey)

	var expiresAt interface{}
	if req.ExpiresAt != "" {
		t, err := time.Parse(time.RFC3339, req.ExpiresAt)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "expires_at must be RFC3339"})
			return
		}
		expiresAt = t
	}

	var id string
	const q = `INSERT INTO api_keys (tenant_id, key_hash, name, expires_at)
	            VALUES ($1, $2, $3, $4) RETURNING id`
	err = h.db.QueryRow(c.Request.Context(), q, req.TenantID, hash, req.Name, expiresAt).Scan(&id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create key"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"id":  id,
		"key": rawKey, // returned once — not stored
	})
}

// RevokeKey handles DELETE /admin/keys/:id.
func (h *AdminHandler) RevokeKey(c *gin.Context) {
	keyID := c.Param("id")
	const q = `UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL`
	tag, err := h.db.Exec(c.Request.Context(), q, keyID)
	if err != nil || tag.RowsAffected() == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "key not found"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"revoked": true})
}

// ListKeys handles GET /admin/keys?tenant_id=<uuid>.
func (h *AdminHandler) ListKeys(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if tenantID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "tenant_id is required"})
		return
	}

	const q = `SELECT id, name, expires_at, revoked_at, created_at
	           FROM api_keys WHERE tenant_id = $1 ORDER BY created_at DESC`
	rows, err := h.db.Query(c.Request.Context(), q, tenantID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "query failed"})
		return
	}
	defer rows.Close()

	type keyRow struct {
		ID        string     `json:"id"`
		Name      string     `json:"name"`
		ExpiresAt *time.Time `json:"expires_at"`
		RevokedAt *time.Time `json:"revoked_at"`
		CreatedAt time.Time  `json:"created_at"`
	}
	var keys []keyRow
	for rows.Next() {
		var k keyRow
		if err := rows.Scan(&k.ID, &k.Name, &k.ExpiresAt, &k.RevokedAt, &k.CreatedAt); err != nil {
			continue
		}
		keys = append(keys, k)
	}
	c.JSON(http.StatusOK, gin.H{"keys": keys})
}

// IssueAdminToken handles POST /admin/token — issues a JWT for a tenant.
// In production this should be protected by a separate admin secret.
func (h *AdminHandler) IssueAdminToken(c *gin.Context) {
	var req struct {
		TenantID string `json:"tenant_id" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
		"tenant_id": req.TenantID,
		"exp":       time.Now().Add(24 * time.Hour).Unix(),
	})
	signed, err := token.SignedString(h.jwtSecret)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to sign token"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"token": signed})
}

func generateKey() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return "mk_" + hex.EncodeToString(b), nil
}
