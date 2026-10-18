# Release Checklist

## Automated checks

Run the repository's regression suite from the root:

```bash
python3 -m unittest test_genie_regression.py
python3 -m py_compile genie.py genie_app.py
```

Review the actual test count and return code. If a file name has changed, discover the canonical test command before substituting a different file.

## Desktop safety checks

1. Verify launch, chat input, response rendering, and the local voice bridge without requiring a paid provider.
2. Confirm that provider keys stay out of logs/Git and that free-first routing and provider health fallback work as intended.
3. Check the dry-run/approval policy before exercising any command that modifies files, deletes data, changes Git remotes, or starts external services.
4. Inspect theme/launcher changes against the canonical config path and keep user state, profiles, credentials, databases, and generated logs out of Git.
5. Review `git diff --check`, the staged diff, and the exact commit before release.

Do not make a release conditional on network-provider credentials. Keep a known-good local launcher and revision for rollback.
