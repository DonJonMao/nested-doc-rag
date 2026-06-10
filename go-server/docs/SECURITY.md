# Security Guide

## Secrets

Secrets must be supplied through environment variables or a secret manager, not committed YAML:

- JWT secret
- bootstrap admin password
- PostgreSQL DSN password
- MinIO secret key
- provider API keys such as `DEEPSEEK_API_KEY` and `OPENAI_API_KEY`

Config summaries and logs redact sensitive keys such as password, token, authorization, api key, secret, and DSN. Authorization headers, refresh tokens, passwords, MinIO secrets, and database passwords must not appear in logs.

## Auth and Authorization

All business APIs use bearer auth. Workspace-scoped services enforce workspace read/write/review checks. Downloads are authorized through file/artifact services and do not bypass workspace scope.

## HTTP Safety

Block 8 adds configurable middleware:

- security headers: `nosniff`, `DENY` frame options, strict CSP, no-referrer, permissions policy
- in-memory per-IP rate limit
- request body size limit
- CORS credentials disabled by default

HSTS is configurable and disabled by default for local development. Enable it only behind HTTPS.

## File Uploads

File upload size and extension/MIME checks remain enforced by FileService. The global body limit is a coarse HTTP guard and does not replace file validation.

## pprof

pprof is disabled by default. If enabled, bind it to `127.0.0.1` or an internal network only:

```yaml
observability:
  pprof_enabled: true
  pprof_addr: "127.0.0.1:6060"
```

pprof is not mounted on the main API router.

## Production Notes

- Terminate TLS at the load balancer or ingress.
- Keep API and worker credentials scoped to required dependencies.
- Do not trust arbitrary `X-Forwarded-For`; Block 8 uses `RemoteAddr` for IP limiting.
- Review audit logs should be retained for compliance and incident review.
