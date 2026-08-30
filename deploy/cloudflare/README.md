# Cloudflare settings for RearVue MCP

These controls live in the Cloudflare account and cannot be applied from this repository:

1. Use **Full (strict)** SSL/TLS mode with a valid certificate on Nginx. Do not use Flexible mode; Nginx deliberately derives `X-Forwarded-Proto` from its own TLS connection.
2. Add a Cache Rule matching URI path `/mcp` or `/mcp/` and set cache eligibility to **Bypass cache**. RearVue and the included Nginx example also send `Cache-Control: no-store`.
3. Confirm WAF/custom rules allow MCP's authenticated `POST`, `GET`, and `DELETE` requests on those two paths. Do not create a broad WAF bypass for the hostname.
4. Preserve the `Authorization` request header to the origin. The included Nginx example forwards it explicitly.
5. Keep synchronous tool work below Cloudflare's default proxied read timeout. RearVue's Gunicorn example uses a 120-second worker timeout, below Cloudflare's current 125-second default.
6. Restrict direct origin access to Cloudflare networks or authenticated origin pulls so clients cannot spoof proxy headers or bypass edge controls.

After deployment, verify that an unauthenticated request receives `401`, a valid MCP initialize request succeeds, and responses show `Cache-Control: no-store` and `X-Accel-Buffering: no`.
