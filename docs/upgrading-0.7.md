# Upgrading to v0.7.x

## Secrets now resolve from EnvironmentFiles only

**Applies to:** hosts upgrading from v0.6.x whose secret values lived only in
the podman secret store (manually built hosts, or hosts where `ots env process`
rewrote `/etc/default/onetimesecret` into `_VAR=ots_var` placeholders).

### Symptom

After upgrading to v0.7.x, a quadlet-managed container crash-loops with a
nil/missing secret environment variable — even though `rots instance shell` and
`rots instance boot-test` show the value present. A green `boot-test`, a dead
container.

### Cause

v0.7.x makes the layered EnvironmentFile set the single source of truth. The
quadlet loads secrets from `/etc/default/onetimesecret` plus the optional
`/etc/default/onetimesecret.local`, and the ad-hoc `podman run` paths
(`instance shell`, `boot-test`, config transform, `run --production`) now layer
those same files with `--env-file` instead of injecting from the podman secret
store with `--secret`.

Before v0.7.x the two paths diverged: the ad-hoc runs pulled secrets from the
podman secret store while the quadlet read only the files. A host whose values
lived only in the store — or whose base env file held `_VAR=ots_var`
placeholders written by `ots env process` — passed `boot-test` on the store
values but gave the quadlet no literal values to read. In v0.7.x both paths read
the files, so a passing `boot-test` now guarantees the quadlet sees the same
environment.

The placeholder transform (`VARNAME=value` -> `_VARNAME=ots_varname` plus a
podman secret) runs only from the opt-in `ots env process` / `ots env push`
commands. It is never invoked by the instance deploy or run paths. Real secret
values are expected to live as literal `KEY=value` lines in the EnvironmentFiles.

### Remedy

Ensure every name listed in `SECRET_VARIABLE_NAMES` has a literal `KEY=value`
line in the merged EnvironmentFile set. Put sensitive values in
`/etc/default/onetimesecret.local` (mode 0600, root-only).

For each name in `SECRET_VARIABLE_NAMES`, pull the value out of the store the
quadlet no longer uses and write it into `.local`:

```bash
# read the value that lived only in the podman secret store
sudo podman secret inspect --showsecret SECRET --format '{{.SecretData}}'

# write it as a literal line the quadlet's EnvironmentFile can read
printf 'SECRET=%s\n' '<value>' | sudo tee -a /etc/default/onetimesecret.local
sudo chmod 600 /etc/default/onetimesecret.local

systemctl restart onetime-web@7043
```

Alternatively, re-run the `lots secrets` provisioning path so `.local` is
populated the standard way.

### New fail-loud behavior

`deploy` and `redeploy` now verify secrets before rendering. If any name in
`SECRET_VARIABLE_NAMES` does not resolve to a non-empty value in the merged
EnvironmentFile set, they refuse and name every missing variable, rather than
producing a silently dead container. Pass `--force` to downgrade the refusal to
a warning and deploy anyway (the application will likely fail at runtime without
the secrets).

### Encryption at rest

Sensitive values currently sit as plaintext lines in
`/etc/default/onetimesecret.local` (mode 0600, root-only). An optional
systemd-creds encryption-at-rest layer is forthcoming and out of scope for this
change.
