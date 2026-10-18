# Security Policy

Desktop Genie can read local files, access provider configurations, invoke terminal/GUI workflows, and control an authenticated local browser bridge. Treat `~/.config/genie`, provider tokens, browser profiles, conversation history, model/router logs, API responses, and local environment files as sensitive. Do not commit credentials, browser profiles, transcripts, or captured source documents; redact secrets and user content from diagnostic output.

Keep secrets in the canonical user config and ensure permissions are owner-only where credentials exist. Review provider routing, fallback behavior, and all command execution paths before changing them. Destructive shell operations must retain approval/dry-run guards across every provider. Never execute commands or upload files solely because an untrusted model response requests it.

Do not enable remote access to the local runtime or browser bridge without explicit authentication, network scoping, and a separate security review. Report vulnerabilities privately to the repository owner with a minimal synthetic reproduction.
