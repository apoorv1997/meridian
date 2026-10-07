package errors

import (
	"errors"
	"fmt"
	"net/http"
)

type GatewayError struct {
	Code    int
	Message string
	Cause   error
}

func (e *GatewayError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("%s: %v", e.Message, e.Cause)
	}
	return e.Message
}

func (e *GatewayError) Unwrap() error { return e.Cause }

func (e *GatewayError) HTTPStatus() int { return e.Code }

func New(code int, msg string, cause error) *GatewayError {
	return &GatewayError{Code: code, Message: msg, Cause: cause}
}

var (
	ErrUnauthorized    = &GatewayError{Code: http.StatusUnauthorized, Message: "unauthorized"}
	ErrForbidden       = &GatewayError{Code: http.StatusForbidden, Message: "forbidden"}
	ErrRateLimited     = &GatewayError{Code: http.StatusTooManyRequests, Message: "rate limit exceeded"}
	ErrNoProviders     = &GatewayError{Code: http.StatusServiceUnavailable, Message: "no providers available"}
	ErrProviderTimeout = &GatewayError{Code: http.StatusGatewayTimeout, Message: "provider timeout"}
	ErrInvalidRequest  = &GatewayError{Code: http.StatusBadRequest, Message: "invalid request"}
	ErrInternalServer  = &GatewayError{Code: http.StatusInternalServerError, Message: "internal server error"}
)

func IsGatewayError(err error) (*GatewayError, bool) {
	var ge *GatewayError
	if errors.As(err, &ge) {
		return ge, true
	}
	return nil, false
}
