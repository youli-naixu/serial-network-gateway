# Security policy

[English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md)

This is an experimental local-network tool. Raw TCP and RFC2217 do not provide authentication or encryption. Keep the PC listener on loopback and place remote endpoints on a controlled network or behind an existing authenticated tunnel. Do not expose a target's bootloader or reset control to the public Internet.

The project does not install kernel drivers, disable Windows security settings, or automatically flash connected hardware. External drivers are separate products and must be obtained from their publishers. Installer signatures, driver signatures and compatibility with current Windows policies are different checks.

Do not include credentials, captured firmware data, complete device inventories or private network topology in issues. Diagnostic reports may contain user-selected endpoints and metadata; inspect them before sharing. The built-in public-tree check is a supplemental check, not a guarantee that all secrets are detected.

For a vulnerability, use GitHub private vulnerability reporting when enabled. If it is unavailable, open a minimal issue requesting a private contact, without exploit details or secrets. There is no guaranteed response SLA for this alpha. No production-support claim is made.
