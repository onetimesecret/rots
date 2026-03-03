Added
~~~~~

- Add ``rots dns`` command group for multi-provider DNS record management
  via dns-lexicon. Commands: ``add``, ``show``, ``update``, ``remove``,
  ``list``. Supports Cloudflare, Route53, DigitalOcean, Gandi, GoDaddy,
  Hetzner, Linode, Namecheap, Porkbun, Vultr, and DNSimple.
- Auto-detect public IP and DNS provider from native env vars
  (e.g. ``CLOUDFLARE_API_TOKEN``, ``AWS_ACCESS_KEY_ID``)
- Track DNS mutations in SQLite audit trail (``dns_records`` and
  ``dns_current`` tables)

Changed
~~~~~~~

- Align pyright pre-commit hook with project dependencies by adding
  ``dns-lexicon`` and ``ots-shared`` to ``additional_dependencies``
