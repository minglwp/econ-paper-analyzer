# Security and data privacy

This application is designed for local use and binds only to `127.0.0.1`.
Do not expose it directly to a LAN or the public internet. Authentication,
CSRF protection, and multi-user isolation are outside the current scope.

Uploaded source files are stored in a private local cache or temporary
directory. Generated reports, configuration, logs, and machine-readable
results can contain research data and must be handled accordingly. The macOS
application stores them under:

```text
~/Library/Application Support/EconPaperAnalyzer/runs
```

Before sharing an analysis bundle, review every included artifact. Report a
security issue through a private GitHub security advisory rather than a public
issue.
