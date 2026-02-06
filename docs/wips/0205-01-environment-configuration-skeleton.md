
# Environment Configuration Skeleton

These are the actual file listings from the main dev/operations workstation.

```bash
$ lsd --tree --dereference ~/.ssh
 .
├──  authorized_keys
├──  clients
│   ├──  afb
│   │   ├──  config
│   │   ├──  id_ed25519
│   │   └── 󰌆 id_ed25519.pub
│   └──  qc
│       ├──  authorized_keys
│       ├──  config
│       ├──  known_hosts
│       ├── 󰁯 known_hosts.old
│       └──  v2
│           ├──  integration
│           │   ├──  config
│           │   ├── 󰌆 integration-keys-us-east-1.pub
│           │   ├── 󰌆 integration-keys.pub
│           │   └── 󰌆 qc-staging-v2.pub
│           ├──  jumphosts
│           ├── 󰌆 key-ca-jumphost.pub
│           ├──  orchestra
│           │   ├──  config
│           │   ├── 󰌆 orchestra-common-2022.pub
│           │   ├── 󰌆 orchestra-default-2022.pub
│           │   └── 󰌆 orchestra-eks-pair.pub
│           ├──  production
│           │   ├──  config
│           │   ├── 󰌆 qc-deploy-key.pub
│           │   ├── 󰌆 qc-deploy-key2.pub
│           │   └── 󰌆 qc-prod-v2.pub
│           └──  sandbox
├──  code_gitpod.d
│   └──  config
├──  config
├──  ctrlmasters
├──  id_ed25519
├── 󰌆 id_ed25519-catalyst-generated.pem
├── 󰌆 id_ed25519.pub
├──  id_rsa-ovh
├── 󰌆 id_rsa-ovh.pub
├──  known_hosts
├── 󰁯 known_hosts.old
├──  onetime
│   ├──  config
│   ├──  config-demo.md
│   ├──  demos
│   │   ├──  config
│   │   ├──  id_ed25519-hz-demokeys
│   │   └── 󰌆 id_ed25519-hz-demokeys.pub
│   ├──  ge
│   ├──  ge-config
│   ├── 󰌆 'GithubApp-4Sentry-Land of a Thousand Lakes Private Key Feb 2025.pem'
│   ├──  id_ed25519
│   ├──  id_ed25519-1pass-hetzner
│   ├── 󰌆 id_ed25519-1pass-hetzner.pub
│   ├──  id_ed25519-catalyst
│   ├── 󰌆 id_ed25519-catalyst.pub
│   ├──  id_ed25519-do
│   ├──  id_ed25519-do-uqcyu
│   ├── 󰌆 id_ed25519-do-uqcyu.pub
│   ├── 󰌆 id_ed25519-do.pub
│   ├──  id_ed25519-ovh
│   ├── 󰌆 id_ed25519-ovh.pub
│   ├── 󰌆 id_ed25519.pub
│   ├──  id_rsa-catalyst
│   ├── 󰌆 id_rsa-catalyst.pub
│   └──  production
│       ├──  config
│       ├──  id_ed25519-hetzner-infra
│       ├── 󰌆 id_ed25519-hetzner-infra.pub
│       ├──  id_ed25519-upcloud
│       └── 󰌆 id_ed25519-upcloud.pub
├── 󰌆 van.2024-03-16.private-key.pem
└──  wireguard-fly
    └──  wireguard-export.zip
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
  IdentityFile              ~/.ssh/onetime/id_rsa-catalyst
  Port                      22
  User                      appuser

Host nz-por-redis-01 nz-redis
  # IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_rsa-catalyst
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
Host eu-nurem-sentry eu-sentry sentry
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  Port                      22
  User                      appuser

Host eu-nurem-proxy-01 eu-proxy
  IdentityFile              ~/.ssh/onetime/id_ed25519
  #IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  Port                      22
  User                      appuser

Host eu-nurem-web-02 eu-web2
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  Port                      22
  User                      appuser

Host eu-nurem-redis-01 eu-redis
  IdentityFile              ~/.ssh/onetime/id_ed25519
  ProxyCommand              ssh eu-nurem-web-02 exec nc %h %p
  Port                      22
  User                      appuser

#
# US
#
Host us-hillsboro-webdirect-01 us-webdirect1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  Port                      22
  User                      appuser

Host us-hillsboro-web-01 us-web1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  Port                      22
  User                      appuser

Host us-hillsboro-redis-01 us-redis1
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/id_ed25519-1pass-hetzner
  ProxyCommand              ssh us-hillsboro-web-01 exec nc %h %p
  Port                      22
  User                      appuser


# -------------------------------------------  DEMOS ---


Host eu-demos-web eu-logto
  HostName                  eu-demos-web.internal

Host eu-demos-db eu-demos-maindb eu-demos-authdb eu-demos-mq
  HostName                  eu-demos-db.internal
  ProxyCommand              ssh eu-demos-web exec nc %h %p


Host eu-demos-*
  IdentityFile              ~/.ssh/onetime/id_ed25519
  IdentityFile              ~/.ssh/onetime/demos/id_ed25519-hz-demokeys
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

Host onetim* ots* eu-* us-* ca-* nz-* au-* uk-* jp-*
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
│       └──  upcloud-dns.md
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
