# Security Policy

## Supported versions
Only the latest release receives security fixes.

## Reporting a vulnerability
Please **do not** open a public issue for security problems. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include the version, your platform, steps to
reproduce and the impact. We aim to acknowledge reports within 7 days.

## Design notes
- Research OS is a single-user application meant to run on your own machine. It listens on 127.0.0.1 by default;
  exposing it to a LAN or the internet is not a supported configuration (see docs/OPS.md).
- API keys are stored locally, removed from child-process environments, redacted from logs and never returned by
  the API.
- Updates are only checked when you click; downloaded installers are verified against the release's sha256 before
  they are run.
