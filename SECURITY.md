# Security

Desktop Companion is experimental software that can expose supported local chats and actions to a paired phone. Before enabling remote access, review the [trust boundaries](README.md#trust-and-security), [phone setup](SETUP.md#phone-access), and [acceptance checklist](COMPATIBILITY.md#destination-mac-acceptance-checklist).

## Reporting

GitHub private vulnerability reporting is the selected channel for public launch, but it has not yet been enabled or verified. Once it is enabled, use the repository's Security tab and select **Report a vulnerability** to privately send the maintainer a report. Do not publish security vulnerabilities, credentials, pairing codes, chat contents, private URLs, or raw logs in a public issue. If you discover a security problem before the channel is available, stop using the affected feature as appropriate and contact the repository owner through a trusted private channel already known to you. If you do not have one, hold sensitive details until the reporting channel is established. No response time or security support commitment is currently published.

The owner must [enable GitHub private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository) at the public visibility change, verify that the reporting option is available, and monitor incoming reports. Until a channel is verified, the project is not ready for public security disclosures.

For non-sensitive defects, an issue can include the source commit, OS and desktop app version, a minimal reproduction with synthetic data, and expected versus observed behavior. Check diagnostics for personal data and secrets before sharing. Never attach live configuration, database files, session records, or unredacted logs.
