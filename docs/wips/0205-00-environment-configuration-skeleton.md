
# Environment Configuration Skeleton

These are the actual file listings from the main dev/operations workstation.

```bash
$ lsd --tree --dereference ~/.ssh
 .
├──  authorized_keys
├──  config
├──  ctrlmasters
├──  id_ed25519
├── 󰌆 id_ed25519-plaintiff-generated.pem
├── 󰌆 id_ed25519.pub
├──  id_rsa-lal
├── 󰌆 id_rsa-lal.pub
├──  known_hosts
├── 󰁯 known_hosts.old
├──  onetime
│   ├──  config
│   ├──  config-demo.md
│   ├──  demos
│   │   ├──  config
│   │   ├──  id_ed25519-ul-demokeys
│   │   └── 󰌆 id_ed25519-ul-demokeys.pub
│   ├──  ge
│   ├──  ge-config
│   ├──  id_ed25519
│   ├──  id_ed25519-fulton-ploptart
│   ├── 󰌆 id_ed25519-fulton-ploptart.pub
│   ├──  id_ed25519-plaintiff
│   ├── 󰌆 id_ed25519-plaintiff.pub
│   ├──  id_ed25519-do
│   ├──  id_ed25519-do-uqcyu
│   ├── 󰌆 id_ed25519-do-uqcyu.pub
│   ├── 󰌆 id_ed25519-do.pub
│   ├──  id_ed25519-lal
│   ├── 󰌆 id_ed25519-lal.pub
│   ├── 󰌆 id_ed25519.pub
│   ├──  id_rsa-plaintiff
│   ├── 󰌆 id_rsa-plaintiff.pub
│   └──  production
│       ├──  config
│       ├──  id_ed25519-ploptart-infra
│       ├── 󰌆 id_ed25519-ploptart-infra.pub
│       ├──  id_ed25519-bananahat
│       └── 󰌆 id_ed25519-bananahat.pub
├── 󰌆 van.2024-03-16.private-key.pem
```

### SSH Config

One of our actual SSH config files. Each host has a unique IP address associated to it (redacted). In some environments the web instances acts as a jumphost for the DB instance which only has an internal private network IP address.

```bash
$ cat .ssh/onetime/config

##
# Wildcard settings are applied at the end. From the manual:
#
#   "Since the first obtained value for each parameter is
#   used, more host-specific declarations should be given
#   near the beginning of the file, and general defaults
#   at the end." -- `$ man 1 ssh`
#

# -------------------------------------------  PRODUCTION ---


# NZ (Por)
#
Host nz-por-web-01 nz-web1
  IdentityFile              ~/.ssh/onetime/id_rsa-plaintiff
  Port                      22
  User                      appuser

Host nz-por-redis-01 nz-redis
  # IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_rsa-plaintiff
  Port                      22
  User                      appuser
 ProxyCommand              ssh nz-por-web-01 exec nc %h %p

#
# CA (Toronto)
#
Host ca-tor-web-02 ca-web2
  # IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-do
  Port                      22
  # User                      root
  User                      appuser

Host ca-tor-web-01 ca-web1
  # IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-do
  Port                      22
  # User                      root
  User                      appuser

Host ca-tor-redis-01 ca-redis
  # IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-do
  Port                      22
  User                      appuser


#
# EU
#
Host ab-noro-sentry ab-sentry sentry
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  Port                      22
  User                      appuser

Host ab-noro-proxy-01 ab-proxy
  IdentityFile              ~/.ssh/onetime/id_ed25519
  #IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  Port                      22
  User                      appuser

Host ab-noro-web-02 ab-web2
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  Port                      22
  User                      appuser

Host ab-noro-redis-01 ab-redis
  IdentityFile              ~/.ssh/onetime/id_ed25519
  ProxyCommand              ssh ab-noro-web-02 exec nc %h %p
  Port                      22
  User                      appuser

#
# US
#
Host ts-lana-webdirect-01 ts-webdirect1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  Port                      22
  User                      appuser

Host ts-lana-web-01 ts-web1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  Port                      22
  User                      appuser

Host ts-lana-redis-01 ts-redis1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-fulton-ploptart
  ProxyCommand              ssh ts-lana-web-01 exec nc %h %p
  Port                      22
  User                      appuser


# -------------------------------------------  DEMOS ---


Host ab-demos-web ab-logto
  HostName                  ab-demos-web.internal

Host ab-demos-db ab-demos-maindb ab-demos-authdb ab-demos-mq
  HostName                  ab-demos-db.internal
  ProxyCommand              ssh ab-demos-web exec nc %h %p


Host ab-demos-*
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/demos/id_ed25519-ul-demokeys
  Port                      22
  User                      appuser
  #User                      root

  # Prevent forwarding locale environment variables to remote hosts
  # This avoids locale-related warnings when connecting to servers
  # without the client's locale settings installed
  SendEnv                   -LANG -LC_*
  UseKeychain               yes
  AddKeysToAgent            yes
  ForwardAgent              yes

  # Specifies that ssh should only use identity keys
  # configured in the ssh configuration files, and not
  # use any identities stored in the ssh-agent.
  IdentitiesOnly            yes


# fly ssh issue --agent [org] [path] [flags]
# Just run redis-insight locally instead of in docker. SSH is denied when its
# just the agent. Need to write the .pem file and run with:
#
#   $ ssh -i ./poop.pem ots-staging-redis
#
Host ots*
  IdentityFile              ~/.ssh/onetime/id_ed25519
  User                      root

Host onetim* ots* ab-* ts-* ca-* nz-* au-* uk-* jp-*
  # Prevent forwarding locale environment variables to remote hosts
  # This avoids locale-related warnings when connecting to servers
  # without the client's locale settings installed
  SendEnv                   -LANG -LC_*
  UseKeychain               yes
  AddKeysToAgent            yes
  ForwardAgent              yes

  # Specifies that ssh should only use identity keys
  # configured in the ssh configuration files, and not
  # use any identities stored in the ssh-agent.
  IdentitiesOnly            yes


### Environment COnfiguration

Each environment represents a group of VPS instances that run onetime secret.


```bash
$ lsd --tree --dereference
 environments
├──  ca
│   ├──  config-v0.23
│   │   ├──  Caddyfile.template
│   │   └──  config.yaml
│   ├──  config-v0.24
│   │   ├──  billing.yaml
│   │   ├──  Caddyfile.template
│   │   ├──  cloud-config-web.yaml
│   │   └──  config.yaml
│   └──  init
│       ├──  prepare-cloud-init.sh
│       └──  redis.conf
├──  demos
│   └──  eu
│       ├──  Caddyfile-lite.template
│       ├──  Caddyfile.template
│       ├──  cloud-config-db.yaml
│       ├──  cloud-config-web.yaml
│       ├──  cloud-config.yaml
│       ├──  config-lite.yaml
│       ├──  config-v0.24
│       │   ├──  auth.yaml
│       │   ├──  billing.yaml
│       │   ├──  Caddyfile.template
│       │   ├──  config.yaml
│       │   ├──  logging.yaml
│       │   └──  puma.rb
│       ├──  config.yaml
│       ├──  jumphost_key
│       ├── 󰌆 jumphost_key.pub
│       ├──  logto
│       │   ├──  INSTALL-podman.md
│       │   └──  INSTALL-systemd.md
│       ├──  oauth2-proxy
│       │   ├──  oauth2-proxy.env
│       │   ├──  oauth2-proxy.service
│       │   ├──  oauth2-proxy.socket
│       │   ├──  README-systemd-socket-units.md
│       │   ├──  README.html
│       │   └──  README.md
│       ├──  prepare-cloud-init-db.sh
│       ├──  prepare-cloud-init-web.sh
│       ├──  README-sso-decision-framework.md
│       ├──  README.md
│       ├──  redis.conf
│       └──  zitadel
│           ├──  PODMAN.md
│           ├──  README-socks-proxy.md
│           ├──  README-sso-handoff.md
│           └──  SETUP-onetimesecret.md
├──  dev
│   └──  caddy
│       ├──  Caddyfile
│       └──  Caddyfile-alt-of-unknown-provinence
├──  eu
│   ├──  config-v0.23
│   │   ├──  Caddyfile.template
│   │   ├──  config.yaml
│   │   └──  redis.conf
│   ├──  config-v0.24
│   │   └──  billing.yaml
│   ├──  init
│   │   └──  prepare-cloud-init.sh
│   └──  proxy
│       ├──  cloud-config-proxy-01.yaml
│       ├──  official-notes.md
│       ├──  proxy-connect-notes.txt
│       └──  sentry
│           └──  docker-compose.yml
├──  infra
│   └──  container-registry
│       └──  cloud-init.yaml
├──  nz
│   ├──  config-v0.23
│   │   ├──  Caddyfile.template
│   │   ├──  config.yaml
│   │   └──  redis.conf
│   ├──  config-v0.24
│   │   └──  billing.yaml
│   ├──  init
│   │   ├──  cloud-config-redis.yaml
│   │   ├──  cloud-config-web.yaml
│   │   └──  prepare-cloud-init.sh
│   └──  openrc-script.sh
├──  uk
│   ├──  config-v0.24
│   │   ├──  auth.yaml
│   │   ├──  billing.yaml
│   │   ├──  Caddyfile.template
│   │   ├──  config.yaml
│   │   ├──  logging.yaml
│   │   └──  puma.rb
│   └──  init
│       ├──  cloud-init-db.yaml
│       ├──  cloud-init-web.yaml
│       ├──  README.md
│       └──  bananahat-dns.md
└──  us
    ├──  allowed-domains
    │   ├──  allowed-domains.py
    │   ├──  customer-how-to-guide-code-sonnet.md
    │   ├──  customer-how-to-guide-k2.md
    │   ├──  domains.txt
    │   ├──  internal-about-cf-for-saas.md
    │   ├──  internal-code-sonnet.md
    │   └──  internal-gunicorn-setup.md
    ├──  config-v0.23
    │   ├──  Caddyfile-webdirect.template
    │   ├──  Caddyfile.template
    │   ├──  config.yaml
    │   └──  redis.conf
    ├──  config-v0.24
    │   ├──  billing.yaml
    │   └──  config.yaml
    └──  init
        ├──  cloud-config-hillsboro-webdirect.yaml
        └──  prepare-cloud-init.sh
```
