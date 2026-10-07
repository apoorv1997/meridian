package middleware

import (
	"context"
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/apoorv1997/meridian/gateway/internal/auth"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// APIKeyAuth extracts the X-API-Key header, authenticates it, and attaches
// tenant_id + api_key_id to both the Gin context and the request context.
func APIKeyAuth(authenticator *auth.Authenticator) gin.HandlerFunc {
	return func(c *gin.Context) {
		rawKey := c.GetHeader("X-API-Key")
		if rawKey == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "X-API-Key header is required"})
			return
		}

		result, err := authenticator.Authenticate(c.Request.Context(), rawKey)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid or expired API key"})
			return
		}

		// Attach to Gin context for handlers downstream.
		c.Set(string(models.ContextKeyTenantID), result.TenantID)
		c.Set(string(models.ContextKeyAPIKeyID), result.KeyID)

		// Propagate into the request context so non-Gin code can read it.
		ctx := context.WithValue(c.Request.Context(), models.ContextKeyTenantID, result.TenantID)
		ctx = context.WithValue(ctx, models.ContextKeyAPIKeyID, result.KeyID)
		c.Request = c.Request.WithContext(ctx)

		c.Next()
	}
}

// TenantIDFromGin retrieves the tenant_id set by APIKeyAuth.
func TenantIDFromGin(c *gin.Context) string {
	v, _ := c.Get(string(models.ContextKeyTenantID))
	s, _ := v.(string)
	return s
}
