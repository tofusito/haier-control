# Security policy

Do not open a public issue for a suspected vulnerability. Use GitHub's private security
advisory flow or contact the maintainer privately through the GitHub profile.

Never expose port 8787 directly to the internet. Protect `secrets/master_key`, keep
`/data` backed up separately, and never commit credentials, session tokens, API tokens or
raw hOn responses. See [deployment](docs/deployment.md) and [security](docs/security.md).
