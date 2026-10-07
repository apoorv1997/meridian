package config

import (
	"fmt"
	"os"
	"strconv"
)

// Load reads all required environment variables and returns a validated Config.
// It fails fast on missing required vars.
func Load() (*Config, error) {
	cfg := &Config{}

	// Database
	cfg.Database.URL = requireEnv("DATABASE_URL")
	cfg.Database.MaxConns = int32(envInt("DB_MAX_CONNS", 20))
	cfg.Database.MinConns = int32(envInt("DB_MIN_CONNS", 2))
	cfg.Database.MaxIdleTime = envInt("DB_MAX_IDLE_SECONDS", 300)

	// Redis
	cfg.Redis.URL = requireEnv("REDIS_URL")
	cfg.Redis.Password = os.Getenv("REDIS_PASSWORD")

	// Gateway
	cfg.Gateway.Port = envInt("GATEWAY_PORT", 8080)
	cfg.Gateway.AdminPort = envInt("GATEWAY_ADMIN_PORT", 8081)
	cfg.Gateway.JWTSecret = requireEnv("JWT_SECRET")
	cfg.Gateway.CircuitErrorThreshold = envFloat("CIRCUIT_ERROR_THRESHOLD", 0.05)
	cfg.Gateway.CircuitWindowSeconds = envInt("CIRCUIT_WINDOW_SECONDS", 60)
	cfg.Gateway.CircuitHalfOpenDelay = envInt("CIRCUIT_HALF_OPEN_DELAY_SECONDS", 30)
	cfg.Gateway.CacheSimilarityThreshold = envFloat("CACHE_SIMILARITY_THRESHOLD", 0.95)
	cfg.Gateway.CacheTTLSeconds = envInt("CACHE_TTL_SECONDS", 3600)
	cfg.Gateway.KafkaBrokers = os.Getenv("KAFKA_BROKERS")
	cfg.Gateway.KafkaTraceTopic = envDefault("KAFKA_TOPIC_TRACES", "meridian.traces")
	cfg.Gateway.RAGServiceURL = os.Getenv("RAG_SERVICE_URL")

	// Providers — at least one must be enabled
	cfg.Providers.Anthropic = ProviderConfig{
		APIKey:  os.Getenv("ANTHROPIC_API_KEY"),
		BaseURL: envDefault("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
		Enabled: os.Getenv("ANTHROPIC_API_KEY") != "",
	}
	cfg.Providers.OpenAI = ProviderConfig{
		APIKey:  os.Getenv("OPENAI_API_KEY"),
		BaseURL: envDefault("OPENAI_BASE_URL", "https://api.openai.com"),
		Enabled: os.Getenv("OPENAI_API_KEY") != "",
	}
	cfg.Providers.Google = ProviderConfig{
		APIKey:  os.Getenv("GOOGLE_API_KEY"),
		BaseURL: envDefault("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com"),
		Enabled: os.Getenv("GOOGLE_API_KEY") != "",
	}

	if !cfg.Providers.Anthropic.Enabled && !cfg.Providers.OpenAI.Enabled && !cfg.Providers.Google.Enabled {
		return nil, fmt.Errorf("config: at least one provider API key must be set (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY)")
	}

	return cfg, nil
}

func requireEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		panic(fmt.Sprintf("config: required environment variable %q is not set", key))
	}
	return v
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		n, err := strconv.Atoi(v)
		if err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		f, err := strconv.ParseFloat(v, 64)
		if err == nil {
			return f
		}
	}
	return def
}
