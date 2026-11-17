# Security policy

## Reporting a problem

Please report security problems **privately**, not in a public issue. Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it, and the version (App and integration).

You'll get an acknowledgement, and fixes are released as a new version with a note in the changelog.

## Supported versions

Only the latest release gets fixes. Home Assistant shows when an update is available.

## How Home Inventory handles your data

[docs/security.md](docs/security.md) describes the security model: who can do what, what stays on your network, what goes to external services (AI providers, barcode catalogs) and what CI checks on every change. In short:

- the App is reached only through Home Assistant (Ingress), never directly from the internet;
- the companion integration only talks to the App on your own network;
- no credentials or secrets are stored in the repository.
