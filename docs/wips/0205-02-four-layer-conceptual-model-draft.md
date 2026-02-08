# 0205-four-layer-conceptual-model.md


## How the Four Layers Interact

**Layer 0 (Name Resolution)** is foundational and SSH-independent. This is the part my model missed entirely by folding it into "configuration management." dnsmasq with per-customer zone files serves every tool on your workstation, not just SSH. `curl`, `ping`, browser access to customer dashboards, monitoring checks all benefit. The SSH config `Hostname` directive is a Layer-0 substitute that only works within SSH, which limits its utility as you add more operational tooling.

The interesting property of Layer 0: it's the only layer where the bastion's view and your workstation's view can silently diverge. If your local dnsmasq says `customer-a-web1` is `10.2.3.4` but the bastion resolves it to `10.2.3.5` (because the customer re-IP'd and only one side was updated), the ProxyJump connection succeeds or fails in a way that's confusing to debug. This is a coupling point between layers that my two-layer model completely hid.

**Layer 1 (SSH Config Management)** depends on Layer 0 outputs (hostnames) and feeds Layer 2 (which hosts are reachable, how). sshclick operates here by parsing and manipulating SSH config as structured data rather than flat text. The value is treating SSH config as a queryable data structure ("show me all hosts for customer B," "which hosts use key X") rather than as files you grep through.

The tension at this layer: generated configs vs. managed configs. If you generate from inventory (the approach I analyzed previously), Layer 1 is a build artifact. If you use sshclick to manipulate configs directly, Layer 1 is the source of truth. These are different operational models. Generation means the inventory is authoritative and configs are disposable/reproducible. Direct manipulation means the SSH config itself is authoritative and needs to be versioned carefully. Mixing them (generating some configs, hand-editing others) creates a "which parts can I safely regenerate?" problem.

**Layer 2 (Multiplexed Operations)** is where my model had a genuine gap. I wasn't treating "act on N hosts simultaneously" as a distinct concern at all. It was implicit in "you have SSH configs, figure it out." But multiplexing has its own failure modes: partial failures across a host set (3 of 5 hosts succeed, now what?), output interleaving, connection limits through a bastion (The Bastion may rate-limit concurrent sessions), and the question of whether multiplexed operations should go through the bastion at all or directly (for bulk operations like deploying a config change across a customer's fleet).

sshmx or similar tools sit at this layer. They consume Layer 1 (SSH config) to know what hosts exist, use Layer 0 (name resolution) to reach them, and produce output that Layer 3 (state tracking) should capture.

**Layer 3 (State Tracking)** is where the bastion re-enters the picture, but now as a participant in one specific layer rather than as a monolithic architectural choice. State tracking encompasses: who accessed what when (audit), what's currently deployed where (inventory state), which hosts are healthy (operational state), and what happened during a session (session recording).

My model conflated all of these into "the bastion provides audit." In reality, the bastion provides session-level audit (access logs, session recording), but deployment state and operational health tracking live elsewhere. A solo admin needs both, and they're different tools with different data models.

## What This Reveals About the Bastion Choice

In the four-layer model, adopting The Bastion (OVH) means:

At Layer 0, you now have two name resolution contexts that must stay synchronized. The bastion resolves targets independently of your workstation. At Layer 1, the bastion adds a ProxyJump directive to every host config but otherwise stays out of config management. At Layer 2, the bastion becomes a bottleneck for multiplexed operations since all sessions funnel through it, and its concurrency limits become your concurrency limits. At Layer 3, the bastion provides strong audit but only for sessions that transit it.

A DIY jumphost is thinner at each layer: at Layer 0 it uses whatever the host OS resolves (no divergence risk), at Layer 1 it's just one more `Host` entry, at Layer 2 it's transparent to multiplexing (it's just a TCP relay), at Layer 3 it provides only what you bolt on.

The four-layer model makes visible a tradeoff my two-layer model obscured: The Bastion's Layer 3 strength (audit, session recording, access control enforcement) comes at the cost of Layer 2 constraints (all operations funneled through a chokepoint) and Layer 0 complexity (dual resolution contexts). For a solo admin doing occasional bulk operations across customer fleets, that Layer 2 constraint matters more than it would for a team where each person connects to one host at a time.

## Access Control as a Cross-Cutting Concern

One thing your four-layer model makes implicit rather than explicit: access control enforcement. It shows up at every layer but lives in none of them. Layer 0 has firewall rules (network-level access). Layer 1 has key selection and `IdentitiesOnly` (credential scoping). Layer 2 inherits whatever access model the layers below enforce. Layer 3 can enforce access retroactively (alerting on unauthorized access) or proactively (The Bastion's ACLs refusing a connection).

The bastion is attractive precisely because it concentrates access control enforcement at a single point rather than distributing it across four layers. The cost is that single point becomes load-bearing for all four layers' security properties.
