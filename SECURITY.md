# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in AstroCrawl, please **do not** report it via a public GitHub Issue.

Instead, send an email to **etoileint@163.com** with details of the vulnerability. Include:

- A description of the vulnerability
- Steps to reproduce
- Affected versions (if known)

I will respond as soon as possible. Once the vulnerability is confirmed and fixed, I will publish a security advisory and credit you (if you wish).

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Scope

Security issues of particular concern include:

- Proxy credential leaks (logs, debug output, child processes)
- API key exposure in configuration or output
- Remote code execution through rule sources or AI inputs
- SSRF through URL handling
- Authentication bypass in HTTP health endpoint
