# Security and data privacy

The packaged macOS application does not start an HTTP listener: its embedded
window calls the analysis process through a local application bridge. The
source-development server binds only to `127.0.0.1`; do not expose it directly
to a LAN or the public internet. Authentication, CSRF protection, and
multi-user isolation are outside the current scope.

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
