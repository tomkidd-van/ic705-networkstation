# Security notes

This project controls radio network sessions and can key PTT when used with TX/station commands.  Treat it as operational radio-control software.

## Network exposure

Do not expose the radio LAN control service or the local rigctl facade directly to the public internet.  Use a trusted local network, VPN or SSH tunnel.

## Credentials

Do not commit radio credentials, packet captures containing credential fields or raw logs containing private station information.  Configure runtime credentials through CLI flags or environment variables:

- `ICOM_HOST`
- `ICOM_USER`
- `ICOM_PASSWORD`

## TX operation

Validate TX paths at low power with safe RF practices.  Station TX audio is intended to be gated by confirmed radio PTT, but operators remain responsible for regulatory compliance and RF safety.

## Reporting concerns

Open a GitHub issue for non-sensitive bugs.  Do not paste real credentials, unredacted captures or private network details into public issues.
