

```
┌─────────────────────────────────────────────────────────────────┐
│ Your Infrastructure                                             │
│                                                                 │
│  ┌─────────────────────────────────────────┐                   │
│  │ The Bastion Server(s)                   │                   │
│  │ (can run active/active cluster)         │                   │
│  │                                         │                   │
│  │  ┌─────────────┐    ┌───────────────┐   │                   │
│  │  │ SSH Daemon  │    │ Plugin System │   │                   │
│  │  │ (ProxyJump  │    │ (MFA, TTL     │   │                   │
│  │  │  endpoint)  │    │  keys, etc)   │   │                   │
│  │  └──────┬──────┘    └───────────────┘   │                   │
│  │         │                               │                   │
│  │  ┌──────▼──────┐    ┌───────────────┐   │                   │
│  │  │ ttyrec      │    │ Access Control│   │                   │
│  │  │ Session     │    │ (who can reach│   │                    │
│  │  │ Recording   │    │  which hosts) │   │                    │
│  │  └─────────────┘    └───────────────┘   │                    │
│  └──────────┬──────────────────────────────┘                    │
│             │ SSH (ProxyJump)                                   │
│             ▼                                                   │
│  ┌─────────────────┐  ┌─────────────────┐                     │
│  │ Prod Server 1   │  │ Prod Server 2   │  (no agent needed)  │
│  └─────────────────┘  └─────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
            ▲
            │ SSH (native client)
┌───────────┴───────────┐
│ Your Terminal         │
│                       │
│ ssh -J bastion prod1  │
└───────────────────────┘
```

**Key characteristic**: Native SSH workflow. You use your normal terminal with ProxyJump—no web UI required [^5][^8].
