# 0205-08-three-layer-model.md
---

# A Three-Layer Model for SSH Infrastructure Management

## The Problem

Managing SSH access across multiple environments produces complexity along several axes simultaneously. Host discovery, credential selection, connection routing, and session auditing all compete for attention inside a single `~/.ssh/config` file or, worse, inside a single operator's memory.

The complexity compounds when environments span jurisdictions and tenant types. A canonical production cluster in the EU, a customer's single-tenant installation in New Zealand, a staging environment in Canada: each has distinct credential boundaries, distinct network paths, and distinct compliance requirements. Data sovereignty demands that these distinctions are encoded into the infrastructure itself, not left to operator recall.

Most discussions of SSH management flatten this into two concerns: "how do I connect" and "how do I control access." This conflation leads to architectural choices where a bastion host is expected to solve configuration management, or where SSH config tooling is expected to solve auditing. Neither works well because these are different problems with different failure modes, different operational cadences, and different tool requirements.

The three-layer model separates SSH infrastructure into functional responsibilities that can be addressed independently, composed flexibly, and degraded gracefully when individual components fail. A control plane sits behind the runtime layers, generating their configuration from a single source of truth.


## The Control Plane: Environment Inventory

The control plane is a Python application backed by SQLite that serves as the authoritative record of all managed infrastructure. It is not a runtime component. dnsmasq and SSH do not consult it when resolving names or establishing connections. They consume its build outputs: generated dnsmasq zone files and generated SSH config fragments.

The inventory encodes three orthogonal dimensions for every host:

**Jurisdiction**: Where the host physically resides and which data sovereignty regime governs it. EU, US, CA, NZ, etc.

**Tenant type**: Whether the environment is canonical (our own multi-tenant production, staging, and support infrastructure) or customer (single-tenant installations built and managed for discrete customers).

**Role**: What the host does within its environment. Web, redis, proxy, sentry, database, etc.

These dimensions determine everything downstream: which DNS subtree a host appears in, which SSH key is presented, which proxy path is used, and which audit requirements apply.

The control plane operates at generation time. When the inventory changes (a new host is provisioned, a customer environment is decommissioned, a host is re-IP'd), the operator regenerates the runtime configuration. The generated artifacts are version-controlled, diffable, and disposable. The inventory is the thing you reason about when infrastructure changes.

### What the control plane replaces

In the prior four-layer model, infrastructure state ("which hosts exist, what's deployed on them") and change history ("what changed in the infrastructure definition and when") were bundled into the top runtime layer alongside session audit. The control plane absorbs both of these concerns. Infrastructure state is the inventory itself. Change history is `git log` on the inventory database and its generated outputs. What remains as a runtime concern is session audit alone.

### Failure characteristics

If the SQLite file corrupts at 2 AM, the last-generated dnsmasq config and SSH config still work. Every existing host remains resolvable and reachable. The operator cannot add new hosts or regenerate configuration until the inventory is restored, but connectivity to existing infrastructure is unaffected. This is the defining characteristic that makes the inventory a control plane rather than a layer: its unavailability does not degrade runtime operations.


## The Domain Hierarchy

The DNS naming schema encodes the control plane's taxonomy directly into name resolution. A single dnsmasq instance serves the internal `.ots` TLD, organized by tenant type and jurisdiction:

```
                           .ots (internal TLD)
                              |
        +---------------------+---------------------+
        |                                           |
   .canonical.ots                              .customer.ots
        |                                           |
   +----+----+                              +-------+-------+
   |         |                              |               |
.prod    .staging                        .acme-corp    .globex
   |         |                              |               |
+---+---+     |                          +---+---+           |
|       |     |                          |       |           |
.eu  .us  .ca .eu                       .eu    .us         .nz
```

A host's fully-qualified internal name reads right to left as a path through the taxonomy: `web-01.eu.prod.canonical.ots` is a web server, in the EU jurisdiction, in the production environment, within canonical (our own) infrastructure. `redis-01.nz.globex.customer.ots` is a Redis instance, in New Zealand, belonging to the Globex customer installation.

This encoding makes the organizational structure visible at the DNS layer. Every tool on the workstation (not just SSH, but also `curl`, monitoring scripts, browser access to dashboards) resolves names through the same taxonomy. The tradeoff: the taxonomy is now load-bearing at Layer 0. Reclassifying an environment (a customer installation absorbed into canonical, a staging environment that needs to serve a second jurisdiction) means renaming hosts in DNS and updating every reference. Flat hostnames like `eu-nurem-web-02` allowed reclassification as a config change; hierarchical names make it a rename.


## The Layers

### Layer 0: Name Resolution

See: 0205-04-layer0-dnsmasq-draft2.md

**Responsibility**: Mapping human-meaningful host identifiers to network addresses, with the name structure itself encoding jurisdiction and tenant type.

**Failure mode**: Stale resolution. The inventory was updated and a host was re-IP'd, but zone files were not regenerated. With a single dnsmasq serving all internal names, the failure affects all tools simultaneously, which surfaces the problem quickly. Falling back to IP addresses remains possible but loses the jurisdictional encoding that the name structure provides.

**Interaction with other layers**: Layer 0 is consumed by Layer 1 (SSH config references hostnames that Layer 0 resolves). When a web instance acts as a jumphost for an internal-only database instance, Layer 0 must resolve both the jumphost's public-facing name and the target's internal name. The jumphost resolves the target independently of the workstation, so both views of Layer 0 must agree, or the ProxyCommand fails in ways that are difficult to debug because the name resolved correctly on the operator's end.


### Layer 1: SSH Config Management

**Responsibility**: Defining how to reach each host: which key to present, which proxy to traverse, which port to use, which user to authenticate as.

SSH config fragments are generated from the same environment inventory that generates Layer 0's zone files. The generation is the key architectural constraint: SSH config and dnsmasq config are never independently managed. They are two projections of a single source. This eliminates the drift class of bugs where a host exists in DNS but not in SSH config, or vice versa.

The generated configs use directory-based organization via `Include`, structured to mirror the domain hierarchy:

```
~/.ssh/config.d/
   canonical/
       prod/
           eu/config
           us/config
           ca/config
       staging/
           eu/config
   customer/
       acme-corp/
           eu/config
           us/config
       globex/
           nz/config
```

Each generated fragment contains the host definitions for its scope: identity file paths, proxy commands (where a web instance jumphosts for a database instance), user, port, and the `IdentitiesOnly yes` directive that prevents credential leakage across environment boundaries.

Credential isolation maps to the taxonomy. Canonical production keys are distinct from canonical staging keys, which are distinct from each customer's keys. The directory structure makes the isolation visible: keys live alongside the config fragments they serve, scoped to the same jurisdictional and tenant-type boundaries.

**Failure mode**: Credential leakage across environments. Without `IdentitiesOnly yes` in each host block, `ssh-agent` offers every loaded key to every host. This is both a security leak (the remote host learns how many other keys you have) and a practical failure (too many offered keys triggers `Too many authentication failures` before the correct key is tried). Because configs are generated, this directive can be enforced universally by the generator rather than relying on per-entry discipline.

A second failure mode specific to generated configs: regeneration with a buggy generator template produces syntactically valid but semantically wrong SSH config. A host gets the wrong key, or a ProxyCommand points at the wrong jumphost. Version-controlling the generated output (not just the inventory) provides the forensic trail to catch this.

**Interaction with other layers**: Layer 1 consumes Layer 0 (hostnames). It feeds Layer 2 with session events that should be tracked. The jumphost pattern (web instance proxying for a database instance with only a private network address) appears here as a `ProxyCommand` directive. Removing or changing the proxy path is a regeneration from the inventory, not a hand-edit, which means the change is captured in the inventory's change history rather than in an SSH config diff alone.


### Layer 2: Audit and State Tracking

**Responsibility**: Recording what happened during SSH sessions and detecting gaps in coverage.

With infrastructure state and change history absorbed by the control plane, this layer narrows to a single concern: session audit. Who connected to which host, when, and what they did.

**Session audit** has two modes depending on network topology:

When a web instance acts as a jumphost (via `ProxyCommand ssh web-01 exec nc %h %p`), the jumphost's `auth.log` captures the proxied connection. This provides "who connected when" for the database tier without dedicated audit infrastructure. It does not capture session content (what commands were executed).

For session content capture, `asciinema` or `ttyrec` wired into the connection path records terminal output. This can be triggered at the SSH config layer (a `LocalCommand` or wrapper script) or at the host level (a forced command or shell wrapper). The choice determines whether audit is operator-side (captured on the workstation, under the operator's control) or host-side (captured on the target, surviving even if the operator's workstation is compromised).

**Failure mode**: Audit gaps. Any connection path that bypasses the normal SSH config (direct IP access during an emergency, a quick `ssh root@10.x.x.x` when the generated config is being regenerated) creates an unaudited session. The gap may be acceptable operationally but unacceptable for compliance. Detection depends on correlating `auth.log` entries on target hosts against the audit trail, which is a reconciliation problem rather than a prevention problem.

**Interaction with other layers**: Layer 2 consumes events from Layers 0 and 1 but does not gate them. If audit infrastructure is down, SSH connections proceed. Audit is append-only by nature; temporary unavailability creates a gap but not data corruption. The control plane's change history (git log on the inventory) complements session audit by providing the "what changed in the infrastructure" context that session logs alone cannot.


## Access Control as a Cross-Cutting Concern

Access control does not sit cleanly in any single layer or in the control plane. It manifests at each one:

**Control plane**: The inventory defines which keys are authorized for which environments. Credential scoping decisions (separate keys per customer, per jurisdiction, per environment class) are made here and enforced through generation.

**Layer 0**: Firewall rules restricting which source IPs can reach which hosts. The jurisdictional encoding in DNS names does not enforce access, but it makes the expected access boundaries legible.

**Layer 1**: Key selection via `IdentityFile` and `IdentitiesOnly` determines which credentials are presented. The generated config enforces that a customer key is never offered to a canonical host and vice versa.

**Layer 2**: Can enforce access retroactively (alerting on unauthorized access patterns) or provide forensic evidence after an incident.

Without a centralized bastion, access control enforcement is distributed across firewall rules, SSH key scoping, and per-host `authorized_keys` files. The control plane's inventory makes this distribution visible and auditable, but the enforcement points remain multiple. The benefit is no single point of failure for access control. The cost is that access policy changes require regeneration and deployment rather than a single bastion ACL update.


## The Graceful Degradation Property

Each layer functions independently when layers above it fail, and tolerates temporary unavailability of the control plane.

- **Control plane unavailable**: All generated configs remain functional. Existing hosts resolve and are reachable. New hosts cannot be added, configuration cannot be regenerated. The operator degrades from "generated" mode to "managed" mode (hand-editing configs directly, which the prior model identified as a distinct operational mode within SSH config management).

- **Layer 0 alone**: Hosts resolve. You can ping them, curl them, access dashboards. The jurisdictional taxonomy is visible in the name structure.

- **Layers 0+1**: You can SSH to any host with the correct key, proxy configuration, and options. Full single-host operational capability.

- **Layers 0+1+2**: Full capability including session audit and compliance coverage.

Working from the top down: if Layer 2 is unavailable, you lose session audit but retain operational capability. If Layer 1 configs are lost, you regenerate from the inventory (or reconstruct from Layer 0 plus credential backups if the control plane is also down). If Layer 0 fails, you fall back to IP addresses.

This property is the architectural test for every component: does adding this component preserve the ability to operate at the layer below when it fails?


## Why Three Layers, Not Four

The prior model included a multiplexing layer (parallel operations across multiple hosts) between SSH config management and state tracking. Removing it reflects two things:

First, for a solo operator connecting to environments individually, multiplexing is an optimization, not a capability. Sequential SSH to each host in a customer's cluster is slower but functionally equivalent. The operational cost of maintaining multiplexing tooling exceeds the time savings for fleet sizes of 2-5 hosts per environment.

Second, the control plane absorbs the host-grouping concern that motivated multiplexing. The inventory knows which hosts belong to which environment. If multiplexing becomes necessary later (fleet sizes grow, or a deployment automation layer is added), the inventory already provides the group definitions. The capability can be re-introduced as tooling that reads the inventory directly, without requiring a dedicated architectural layer.


## Implications for Implementation

The three-layer model with a control plane clarifies the build order:

1. **Control plane first**: Define the SQLite schema. Populate it with existing hosts from the current SSH configs and environment directories. The schema must encode jurisdiction, tenant type, role, network address, credential path, and proxy topology.

2. **Generators second**: Write the Python that reads the inventory and emits dnsmasq zone files and SSH config fragments. The generators are the integration surface where the control plane meets the runtime layers. Their correctness determines whether the generated configs are trustworthy.

3. **dnsmasq configuration third**: Single instance, `.ots` TLD, zone files generated from step 2. Validate that every host in the inventory resolves correctly.

4. **SSH config validation fourth**: Verify that generated SSH configs connect to the right hosts with the right keys through the right proxy paths. This is where `IdentitiesOnly` enforcement and credential isolation are tested.

5. **Audit last**: Session logging can be layered on after the connectivity layers are solid. It depends on Layers 0 and 1 being stable and correct.

The version control strategy follows naturally: the inventory database, the generator scripts, and the generated outputs all go into version control. The inventory is the source of truth; the generated outputs are reproducible artifacts checked in for forensic diffing and for the "control plane unavailable" degradation scenario.
