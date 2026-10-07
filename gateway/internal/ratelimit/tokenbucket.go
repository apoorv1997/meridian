package ratelimit

// BucketConfig describes the token bucket parameters for a tenant or API key.
type BucketConfig struct {
	Capacity   float64 // max tokens in bucket
	RefillRate float64 // tokens refilled per second
}

// DefaultBucketConfig returns sensible defaults (1000 rpm ≈ 16.67/s).
func DefaultBucketConfig() BucketConfig {
	return BucketConfig{
		Capacity:   100,
		RefillRate: 16.67,
	}
}
