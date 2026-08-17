# Security Policy

## Supported Versions

We actively maintain and provide security patches for the latest released versions of this package:

| Version | Supported          |
| ------- | ------------------ |
| >= 0.10.x | :white_check_mark: |
| < 0.10.x  | :x:                |

## Reporting a Vulnerability

We take the security of our Model Context Protocol (MCP) servers seriously. If you believe you have found a security vulnerability in this project, please report it responsibly.

**Please do not report security vulnerabilities through public GitHub issues.**

### How to Report

1. Email your findings to **reachsuren@gmail.com** or submit a private security advisory through the repository's **[Security Advisory](https://github.com/surendranb/google-search-console-mcp/security/advisories/new)** tab.
2. Include a detailed description of the vulnerability, steps to reproduce, and a proof of concept (PoC) if available.
3. We will acknowledge receipt within 48 hours and work on a fix as quickly as possible.

### Security Principles

- **Zero-PII**: Telemetry collects structural performance metrics only and never logs user search terms, auth tokens, or queries.
- **Read-Only Scope**: The server performs read-only information projection and executes no destructive database operations.
- **Opt-Out**: Strictly honors `DO_NOT_TRACK=1` and `MCP_TELEMETRY_OPT_OUT=1`.
