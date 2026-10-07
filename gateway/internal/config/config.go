package config

// Config is the top-level configuration for the gateway.
type Config struct {
	Database  DatabaseConfig
	Redis     RedisConfig
	Gateway   GatewayConfig
	Providers ProvidersConfig
}

type DatabaseConfig struct {
	URL         string
	MaxConns    int32
	MinConns    int32
	MaxIdleTime int // seconds
}

type RedisConfig struct {
	URL      string
	Password string
}

type GatewayConfig struct {
	Port      int
	AdminPort int
	JWTSecret string

	// Circuit breaker
	CircuitErrorThreshold float64 // fraction, e.g. 0.05 = 5%
	CircuitWindowSeconds  int
	CircuitHalfOpenDelay  int // seconds before trying half-open

	// Semantic cache
	CacheSimilarityThreshold float64
	CacheTTLSeconds          int

	// Kafka for OTel trace emission
	KafkaBrokers    string
	KafkaTraceTopic string

	// RAG retrieval service. Empty = RAG and semantic cache disabled.
	RAGServiceURL string
}

type ProvidersConfig struct {
	Anthropic ProviderConfig
	OpenAI    ProviderConfig
	Google    ProviderConfig
}

type ProviderConfig struct {
	APIKey  string
	BaseURL string
	Enabled bool
}
