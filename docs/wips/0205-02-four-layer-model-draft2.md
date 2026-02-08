# 0205-four-layer-model-draft2.txt
---

# A Four-Layer Model for SSH Infrastructure Management

## The Problem

Managing SSH access across multiple environments -- company production, staging, and separate customer installations -- produces complexity along several axes simultaneously. Host discovery, credential selection, connection routing, session auditing, and bulk operations all compete for attention inside a single `~/.ssh/config` file or, worse, inside a single operator's memory.

Most discussions of SSH management flatten this into two concerns: "how do I connect" and "how do I control access." This conflation leads to architectural choices where a bastion host is expected to solve configuration management, or where SSH config tooling is expected to solve auditing. Neither works well because these are different problems with different failure modes, different operational cadences, and different tool requirements.

The four-layer model separates SSH infrastructure into functional responsibilities that can be addressed independently, composed flexibly, and degraded gracefully when individual components fail.

## The Layers

### Layer 0: Name Resolution

**Responsibility**: Mapping human-meaningful host identifiers to network addresses.

This is the foundation layer, and it sits below SSH entirely. When you type `ssh customer-a-web1`, something must resolve that name to an IP address before any SSH behavior begins.

The simplest implementation is the `Hostname` directive inside SSH config blocks. Each host entry includes `Hostname 10.2.3.4`, and resolution is handled entirely within SSH. This works but is SSH-scoped: other tools (`curl`, `ping`, monitoring scripts, browser access to customer dashboards) cannot use the same name mappings.

A broader implementation uses local DNS via dnsmasq with per-customer zone files. Zone files can be generated from the same inventory that feeds higher layers. This gives every tool on the workstation consistent name resolution, not just SSH.

**Failure mode**: Stale resolution. The inventory says `customer-a-db1` is `10.2.3.4` but the host was re-IP'd last week. With SSH-scoped resolution, the failure is obvious (connection refused or wrong host key). With DNS-based resolution, the failure affects all tools simultaneously, which is actually better -- you notice it faster because more things break.

**Interaction with other layers**: Layer 0 is consumed by Layer 1 (SSH config references hostnames that Layer 0 resolves) and indirectly by Layer 2 (multiplexed operations need to reach the right hosts). When a bastion is involved, a critical coupling emerges: the bastion resolves target hostnames independently of your workstation. If the bastion's view of Layer 0 diverges from yours, connections succeed or fail in ways that are difficult to debug because the name resolved correctly on your end.

### Layer 1: SSH Config Management

**Responsibility**: Defining how to reach each host -- which key to present, which proxy to traverse, which port to use, which user to authenticate as.

This layer is where tools like sshclick and sshtmux operate. They treat SSH config not as a flat text file but as structured data that can be queried, filtered, grouped, and generated. The `Include` directive with directory-based organization (`config.d/customer-a/`, `config.d/customer-b/`) provides the filesystem structure; config management tools provide the manipulation interface on top.

Layer 1 has two operational modes that should not be mixed carelessly:

**Generated configs**: An inventory file (YAML, TOML, SQLite) is the source of truth. A script reads inventory and emits SSH config fragments. The configs are build artifacts -- disposable and reproducible. The inventory is what you version-control and diff.

**Managed configs**: The SSH config files themselves are the source of truth. Tools like sshclick parse and modify them directly. The configs are what you version-control and diff.

Mixing these modes (generating some directories, hand-editing others) creates a "which parts can I safely regenerate?" problem that gets worse over time.

**Failure mode**: Credential leakage across environments. Without `IdentitiesOnly yes` in each host block, `ssh-agent` offers every loaded key to every host. This is both a security leak (the remote host learns how many other keys you have) and a practical failure (too many offered keys triggers `Too many authentication failures` before the correct key is tried). Layer 1 is where credential isolation per environment class (company-prod, customer-a, customer-b) is enforced through `IdentityFile` paths and `IdentitiesOnly` directives.

**Interaction with other layers**: Layer 1 consumes Layer 0 (hostnames) and is consumed by Layer 2 (multiplexed operations need to know which hosts exist and how to reach them). The bastion appears at this layer as a `ProxyJump` directive. This is significant because it means removing the bastion from the path is a Layer 1 config change, not an architectural upheaval -- you edit or regenerate configs without the ProxyJump line, and SSH works directly (assuming firewall rules cooperate).

### Layer 2: Multiplexed Operations

**Responsibility**: Acting on multiple hosts simultaneously -- running a command across a customer's fleet, opening parallel sessions, deploying a configuration change to N hosts at once.

This is the operational layer. Layers 0 and 1 are about defining and reaching infrastructure. Layer 2 is about doing work across it. Tools like sshmx sit here, consuming the host definitions from Layer 1 to provide discovery, filtering, and parallel execution.

Layer 2 is where the "solo admin managing multiple customer environments" constraint bites hardest. Without multiplexing, every operation on a customer's five-host cluster requires five sequential SSH sessions. With multiplexing, you define the host group once and operate on it as a unit.

**Failure mode**: Partial failure across a host set. Three of five hosts succeed, two fail. Layer 2 tooling must surface this clearly, and the operator must be able to retry the failed subset without re-running the successful operations. This is a different failure mode from Layer 0 (name resolution) or Layer 1 (config correctness) -- the configs are right, the names resolve, but the operation itself fails on some targets.

**Interaction with other layers**: Layer 2 depends on Layer 1 for host discovery and connection parameters, on Layer 0 for name resolution, and feeds Layer 3 with operational events that should be tracked. When a bastion is in the path, Layer 2 is constrained by the bastion's concurrency limits. Five parallel SSH sessions through a bastion that rate-limits to three concurrent connections means two operations wait. For bulk operations (deploying a config change across a customer fleet), this bottleneck may justify bypassing the bastion temporarily, which has audit implications at Layer 3.

### Layer 3: State Tracking

**Responsibility**: Recording what happened, what the current state of the infrastructure is, and what has changed.

Layer 3 encompasses several kinds of state that are often conflated:

**Session audit**: Who connected to which host, when, and what they did. This is the compliance-facing concern. A bastion with session recording (The Bastion's ttyrec, or DIY equivalents like asciinema wired to a jumphost) provides this. Without a bastion, you're relying on `auth.log` on individual hosts, which gives "who connected when" but not "what they did."

**Infrastructure state**: Which hosts exist, what's deployed on them, when they were last updated. This is the operational concern. It may live in the same inventory that feeds Layer 1, or in a separate state tracking system.

**Change history**: What changed in the infrastructure definition and when. If the Layer 1 configs and the Layer 0 zone files are version-controlled, `git log` provides this. The value is forensic: during an incident, you can determine whether a config change preceded the problem.

**Failure mode**: Audit gaps. If Layer 3 depends on the bastion for session audit, any operation that bypasses the bastion (direct SSH during an emergency, multiplexed operations that skip ProxyJump for performance) creates an audit gap. The gap may be acceptable operationally but unacceptable for compliance. The architecture must account for how these gaps are detected and documented.

**Interaction with other layers**: Layer 3 consumes events from all other layers but should not gate them. If Layer 3's state tracking system is down, operations at Layers 0-2 should continue unimpeded. State tracking is append-only by nature; temporary unavailability creates a gap but not data corruption.

## Access Control as a Cross-Cutting Concern

Access control does not sit cleanly in any single layer. It manifests at each one:

- **Layer 0**: Firewall rules that restrict which source IPs can reach which hosts (network-level access control).
- **Layer 1**: Key selection via `IdentityFile` and `IdentitiesOnly` determines which credentials are presented. Separate keys per environment class mean a compromised key's blast radius is bounded.
- **Layer 2**: Inherits whatever access model Layers 0 and 1 enforce. Multiplexed operations amplify both authorized and unauthorized access.
- **Layer 3**: Can enforce access retroactively (alerting on unauthorized access patterns) or proactively (a bastion's ACLs refusing a connection before it reaches the target).

A bastion concentrates access control enforcement at a single chokepoint that spans all four layers. The cost is that the single point becomes load-bearing for all four layers' security properties. The benefit is that access policy changes happen in one place rather than being distributed across firewall rules, SSH configs, and per-host `authorized_keys` files.

## The Graceful Degradation Property

Each layer should function independently when layers above it fail, and should tolerate temporary unavailability of layers it depends on.

- **Layer 0 alone**: Hosts resolve. You can ping them, curl them, SSH to them by IP if you know the credentials.
- **Layers 0+1**: You can SSH to any host with the correct key, proxy configuration, and options. Full single-host operational capability.
- **Layers 0+1+2**: You can operate on host groups. Full multi-host operational capability.
- **Layers 0+1+2+3**: Full capability including audit, state tracking, and compliance coverage.

Working from the top down: if Layer 3 is unavailable, you lose audit but retain operational capability. If Layer 2 tooling breaks, you fall back to sequential single-host SSH. If Layer 1 configs are lost, you can regenerate them from inventory (if using generated configs) or reconstruct them from Layer 0 plus credential backups. If Layer 0 fails, you fall back to IP addresses.

This property is the architectural test for every tool choice: does adding this tool at Layer N preserve the ability to drop to Layer N-1 and still operate?

## Implications for Tool Selection

The four-layer model clarifies which tools compete and which compose.

Tools that span multiple layers (The Bastion touches Layers 0, 1, and 3; Bastillion touches Layers 1, 2, and 3) create coupling between layers that would otherwise be independent. This coupling may be desirable (centralized access control) or problematic (a single component's failure degrades multiple layers simultaneously).

Tools that are layer-specific (dnsmasq at Layer 0, sshclick at Layer 1, sshmx at Layer 2) compose cleanly but require the operator to integrate them. The integration work is the cost of decoupling.

For a solo operator managing multiple customer environments, the bias should be toward layer-specific tools with thin integration, because the failure blast radius of a multi-layer tool is larger than what one person can debug and recover from simultaneously. A multi-layer tool that fails at 2 AM takes out multiple capabilities at once; a Layer 2 tool that fails at 2 AM means you fall back to sequential SSH while Layer 0, Layer 1, and Layer 3 continue operating.
