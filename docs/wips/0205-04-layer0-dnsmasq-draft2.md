# 0205-04-layer0-dnsmasq-draft2.md

---

# Layer 0: Name Resolution (The Truth Layer)

Layer 0 acts as the authoritative source of network truth. It maps the semantic requirements of data sovereignty (Jurisdiction + Tenant Type) to concrete network addresses.

Unlike previous iterations involving multiplexers, this layer does not handle routing logic or user aliases. Its sole responsibility is to answer the question: _"Where is the authoritative interface for this fully qualified identity?"_

## Architecture: The Generated Truth

Configuration is not written by hand. It is generated from the **Environment Inventory (Python+SQLite)**, which serves as the control plane.

- **Layer 0 (DNS):** Defines the `FQDN` -> `IP` mapping. Enforces sovereignty borders.
- **Layer 1 (SSH):** Defines the `Alias` -> `FQDN` mapping. Provides the user interface.

## Domain Hierarchy

We use a strict hierarchical schema to encode sovereignty boundaries directly into the hostname. This ensures that a host's name dictates its legal and logical location.

```text
                            .abc (Internal TLD)
                               │
         ┌─────────────────────┴─────────────────────┐
         │                                           │
    .companyproper.abc                              .customer.abc
   (Multi-tenant/SaaS)                        (Single-tenant/Private)
         │                                           │
    ┌────┴────┐                              ┌───────┴───────┐
    │         │                              │               │
.prod    .staging                        .acme-corp    .globex
    │         │                              │               │
┌───┴───┐     │                          ┌───┴───┐           │
│       │     │                          │       │           │
.eu  .us  .ca .eu                       .eu    .us         .nz
```

## Naming Convention

Every host is assigned a **Mutable FQDN** at Layer 0.

`server-id.jurisdiction.environment.tenant-type.abc`

| Component        | Values                         | Purpose                                                 |
| :--------------- | :----------------------------- | :------------------------------------------------------ |
| **server-id**    | `web-01`, `db-02`, `node-8f7a` | Unique identifier within the pool.                      |
| **jurisdiction** | `eu`, `us`, `ca`, `nz`         | **Sovereignty Boundary.** Determines physical location. |
| **environment**  | `prod`, `staging`, `support`   | Lifecycle stage (for Canonical).                        |
| **tenant-type**  | `companyproper`, `customer`        | **Data Owner.** Determines isolation level.             |

### Customer Variation

For single-tenant customer installations, the `customer-id` replaces the environment segment to ensure namespace isolation.
`server-id.jurisdiction.customer-id.customer.abc`

## Generated Dnsmasq Configuration

The inventory generator produces a flat list of explicit address mappings. This eliminates ambiguity and ensures that if a host is re-classified (e.g., moved from Staging to Prod), its DNS resolution path changes to reflect the new reality.

```conf
# /etc/dnsmasq.d/ots.conf

# Global settings
port=5353
listen-address=127.0.0.1
bind-interfaces
local=/ots/
domain-needed
bogus-priv

# ─────────────────────────────────────────────────────────
# CANONICAL INFRASTRUCTURE
# ─────────────────────────────────────────────────────────

# EU Production (Strict GDPR)
address=/web-01.eu.prod.companyproper.abc/10.1.1.10
address=/db-01.eu.prod.companyproper.abc/10.1.1.20

# US Production
address=/web-01.us.prod.companyproper.abc/10.2.1.10

# Staging (EU)
address=/web-01.eu.staging.companyproper.abc/10.1.2.10

# ─────────────────────────────────────────────────────────
# CUSTOMER INSTALLATIONS (Sovereign Single-Tenancy)
# ─────────────────────────────────────────────────────────

# Acme Corp (EU Jurisdiction)
address=/app-01.eu.acme-corp.customer.abc/10.10.1.10
address=/db-01.eu.acme-corp.customer.abc/10.10.1.20

# Globex (NZ Jurisdiction)
address=/app-01.nz.globex.customer.abc/10.11.1.10
```

## Decoupling: The Stable Alias Pattern

A key feature of this model is that users **never type the FQDNs defined above**.

Layer 0 provides the "Mutable Implementation" (where the server is right now). Layer 1 (SSH Config) provides the "Stable Identifier" (what the server is called).

| Layer         | Responsibility | Example                                      | Source                    |
| :------------ | :------------- | :------------------------------------------- | :------------------------ |
| **Inventory** | **Map**        | `eu-web-01` → `web-01.eu.prod.companyproper.abc` | SQLite Database           |
| **Layer 1**   | **Interface**  | `Host eu-web-01`                             | Generated `~/.ssh/config` |
| **Layer 0**   | **Route**      | `web-01.eu.prod.companyproper.abc` → `10.1.1.10` | Generated `dnsmasq.conf`  |

This separation allows us to reclassify a server (e.g., moving Acme to a new jurisdiction) by updating the Inventory. The Generator then:

1.  Updates **Layer 0** to route the new FQDN.
2.  Updates **Layer 1** to point the existing Stable Alias to the new FQDN.

The user continues to type `ssh acme-web` without interruption.

```

```
