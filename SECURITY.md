# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | Yes |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security issue — such as an API key exposure, injection vulnerability, or authentication bypass — please report it privately:

1. Go to the [Security tab](../../security/advisories/new) of this repository and open a private advisory, **or**
2. Email the maintainer directly (address on the GitHub profile)

Include as much detail as possible:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You can expect an acknowledgment within 48 hours and a resolution or status update within 7 days.

## Security Notes for Self-Hosters

- Store API keys only in `.env` — never commit them to git (`.env` is gitignored)
- The Grafana admin password must be at least 8 characters (enforced by Grafana 12+)
- The fish-logger app has no built-in authentication — run it behind a firewall or reverse proxy with auth if exposed to the internet
- The SQLite database is mounted read-only into the Grafana container; the fish-logger container has read-write access
