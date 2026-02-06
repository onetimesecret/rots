# 0205-layer0-dnsmasq.md
---

# Layer 0: Name resolution (dnsmasq)

We will model dnsmasq configuration around identity-based routing with a Single Dnsmasq with Explicit Client Configuration. Data sovereignty is top priority so clear organizing by jurisdiction and whether canonical (our own multi-tenant production/staging environment and support infra) or customer (the custom single-tenant installations we build and manage for discrete customers).


Based on our existing structure [^1] and the single-dnsmasq approach, here's a DNS naming schema that encodes jurisdiction and tenant type:

## Domain Hierarchy

```
                            .ots (internal TLD)
                               │
         ┌─────────────────────┴─────────────────────┐
         │                                           │
    .canonical.ots                              .customer.ots
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

```
<service>.<jurisdiction>.<environment>.<tenant-type>.ots
```

| Component      | Values                           | Purpose                         |
| -------------- | -------------------------------- | ------------------------------- |
| `service`      | `api`, `db`, `worker`, `bastion` | What it does                    |
| `jurisdiction` | `eu`, `us`, `ca`, `nz`, `uk`     | Data sovereignty boundary       |
| `environment`  | `prod`, `staging`, `support`     | Lifecycle stage                 |
| `tenant-type`  | `canonical`, `customer`          | Your infra vs customer installs |

For customer installations, replace `environment` with customer ID:

```
<service>.<jurisdiction>.<customer-id>.customer.ots
```

## Generated Dnsmasq Configuration

```conf
# /etc/dnsmasq.d/ots.conf

# Global settings
port=5353
listen-address=127.0.0.1
bind-interfaces

local=/ots/
domain-needed
bogus-priv

local-ttl=60
neg-ttl=10

# ─────────────────────────────────────────────────────────
# CANONICAL: Multi-tenant production
# ─────────────────────────────────────────────────────────

# EU Production (GDPR jurisdiction)
address=/api.eu.prod.canonical.ots/10.1.1.10
address=/db.eu.prod.canonical.ots/10.1.1.20
address=/worker.eu.prod.canonical.ots/10.1.1.30
address=/bastion.eu.prod.canonical.ots/10.1.1.5

# US Production
address=/api.us.prod.canonical.ots/10.2.1.10
address=/db.us.prod.canonical.ots/10.2.1.20

# CA Production
address=/api.ca.prod.canonical.ots/10.3.1.10
address=/db.ca.prod.canonical.ots/10.3.1.20

# EU Staging
address=/api.eu.staging.canonical.ots/10.1.2.10
address=/db.eu.staging.canonical.ots/10.1.2.20

# Support infrastructure (internal tools, CI, etc.)
address=/ci.eu.support.canonical.ots/10.1.3.10
address=/monitoring.eu.support.canonical.ots/10.1.3.20

# ─────────────────────────────────────────────────────────
# CUSTOMER: Single-tenant installations
# ─────────────────────────────────────────────────────────

# Acme Corp (EU jurisdiction, GDPR applies)
address=/api.eu.acme-corp.customer.ots/10.10.1.10
address=/db.eu.acme-corp.customer.ots/10.10.1.20
address=/bastion.eu.acme-corp.customer.ots/10.10.1.5

# Acme Corp (US jurisdiction, separate data residency)
address=/api.us.acme-corp.customer.ots/10.10.2.10
address=/db.us.acme-corp.customer.ots/10.10.2.20

# Globex (NZ jurisdiction)
address=/api.nz.globex.customer.ots/10.11.1.10
address=/db.nz.globex.customer.ots/10.11.1.20

# ─────────────────────────────────────────────────────────
# Provider-specific upstream forwarding
# ─────────────────────────────────────────────────────────

server=/internal.aws-eu-west-1/169.254.169.253
server=/internal.aws-us-east-1/169.254.169.253
server=/internal.gcp-europe-west1/169.254.169.254
```

## Inventory Schema

```yaml
dnsmasq:
  domain: ots
  listen: 127.0.0.1
  port: 5353
  ttl: 60

  canonical:
    prod:
      eu:
        - { service: api, ip: 10.1.1.10 }
        - { service: db, ip: 10.1.1.20 }
        - { service: worker, ip: 10.1.1.30 }
        - { service: bastion, ip: 10.1.1.5 }
      us:
        - { service: api, ip: 10.2.1.10 }
        - { service: db, ip: 10.2.1.20 }
      ca:
        - { service: api, ip: 10.3.1.10 }
        - { service: db, ip: 10.3.1.20 }
    staging:
      eu:
        - { service: api, ip: 10.1.2.10 }
        - { service: db, ip: 10.1.2.20 }
    support:
      eu:
        - { service: ci, ip: 10.1.3.10 }
        - { service: monitoring, ip: 10.1.3.20 }

  customer:
    acme-corp:
      jurisdictions: [eu, us]
      eu:
        - { service: api, ip: 10.10.1.10 }
        - { service: db, ip: 10.10.1.20 }
        - { service: bastion, ip: 10.10.1.5 }
      us:
        - { service: api, ip: 10.10.2.10 }
        - { service: db, ip: 10.10.2.20 }
    globex:
      jurisdictions: [nz]
      nz:
        - { service: api, ip: 10.11.1.10 }
        - { service: db, ip: 10.11.1.20 }

  upstreams:
    - { domain: internal.aws-eu-west-1, server: 169.254.169.253 }
    - { domain: internal.aws-us-east-1, server: 169.254.169.253 }
    - { domain: internal.gcp-europe-west1, server: 169.254.169.254 }
```

## Short Aliases (Matching Existing Conventions)

Your current hostnames use `eu-`, `us-`, `nz-` prefixes [^1]. Add CNAME-style aliases:

```conf
# Short aliases for operator convenience
# Format: <jurisdiction>-<service>-<env> or <jurisdiction>-<customer>-<service>

# Canonical
cname=eu-api-prod,api.eu.prod.canonical.ots
cname=eu-db-prod,db.eu.prod.canonical.ots
cname=us-api-prod,api.us.prod.canonical.ots

# Customer
cname=eu-acme-api,api.eu.acme-corp.customer.ots
cname=nz-globex-db,db.nz.globex.customer.ots
```

## Data Sovereignty Validation

Your inventory system can enforce jurisdiction rules:

```python
def validate_jurisdiction(host, customer):
    """Ensure customer data stays in permitted jurisdictions."""
    allowed = customer.get("allowed_jurisdictions", ["eu", "us", "ca", "nz", "uk"])
    if host["jurisdiction"] not in allowed:
        raise ValueError(
            f"Customer {customer['id']} not permitted in {host['jurisdiction']}"
        )
```

[^1]:
    [0205-environment-configuration-skeleton](0205-environment-configuration-skeleton.txt) (100%)
    https://kagi.com/assistant/91a7068c-62b7-4a86-b3d5-b5adc7678baa
