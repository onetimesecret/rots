# 0205-layer1-sshtmux.md
---

# Layer 1: SSH config management (sshtmux)

Session workspace management. "Open a tmux layout with panes for this customer's web and redis hosts" is an ergonomic concern, not an architectural one. sshtmux and sshmx both do this. The value is muscle-memory: sshmx group globex-nz opens the right panes to the right hosts. Without such a tool, you're typing ssh web-01.nz.globex.customer.ots and ssh redis-01.nz.globex.customer.ots in separate panes. Slower, not harder.
