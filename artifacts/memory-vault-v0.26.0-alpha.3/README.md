# Memory Vault v0.26.0-alpha.3 synthetic A/B follow-up

Reproduction artifact for
[TimeToBuildBob/TimeToBuildBob#7](https://github.com/TimeToBuildBob/TimeToBuildBob/issues/7).
It re-runs the alpha.1 negative case from
[TimeToBuildBob/TimeToBuildBob#8](https://github.com/TimeToBuildBob/TimeToBuildBob/pull/8)
against the public `v0.26.0-alpha.3` Review ZIP.

Synthetic data only. It does not contain identities, encryption keys,
invitations, credentials, private history, or the disposable runtime state.

## Pinned input

- Review ZIP: `memory-vault-review-v0.26.0-alpha.3.zip`
- Review ZIP SHA-256:
  `fccc8d4d5aead869e42de3ec744fcdafcf224fd19a63e7e378eed3c98960251c`
- Source commit: `d657d8381326693673426e66373d47e80f73be40`
- Runtime: Ubuntu 24.04.3 LTS, CPython 3.12.3
- Topology: Starlette `TestClient` loopback URLs, one authority, one relay, two endpoints

## Command

From an extracted Review ZIP and the hash-locked virtual environment described
in the release quickstart:

```sh
ROOT=/tmp/memory-vault-pilot-20260901
cd "$ROOT"
"$ROOT/network-env/bin/python" \
  memory_vault_v026_alpha3_synthetic_ab.py \
  > memory_vault_v026_alpha3_synthetic_ab.result.json
sha256sum \
  memory_vault_v026_alpha3_synthetic_ab.py \
  memory_vault_v026_alpha3_synthetic_ab.result.json
```

The script keeps generated identity/key/vault/relay state beneath a temporary
directory and prints only the key-free JSON summary committed here. Change the
absolute `ROOT` constant when reproducing elsewhere.

## Cancellation probe (same negative case as alpha.1)

- Query: `retry the fixture probe`
- Limit: `4` (the native Agent facade's fixed page limit)
- Original goal deliberately contains the query phrase.
- Superseding cancellation is deliberately different:
  `Synthetic cancellation: the retry goal is cancelled; do not execute it.`

Surfaces checked:

- ordinary Vault recall (`limit=4` and `limit=1`)
- Vault `handoff` (`limit=4` and `limit=1`)
- six-operation Agent facade ordinary recall and `handoff:true`
- Agent facade pagination via `next_cursor`

Observed in this rerun:

- ordinary Vault recall ranks the current cancellation first (`score_milli=1118`)
  ahead of the superseded goal (`score_milli=3194`);
- Vault `handoff` uses the same current-before-history order;
- `limit=1` ordinary recall and `handoff` both return only the cancellation;
- Agent ordinary recall and `handoff:true` both return the cancellation first
  with `status=current`; the superseded goal is still present later;
- Agent `next_cursor` page 2 continues the frozen ID list and does not repeat
  page-1 IDs.

The superseded goal remains queryable. The score of the historical goal is
still higher; the current-record tier is what now puts cancellation first.

Option 2 from the #7 follow-up (public HTTPS network-test ZIP) was not run:
no privately supplied one-time code was present.

## Hashes

```txt
9715278001e2f8d826d94ae60aafcd32fb3d7a86c3a98ac5d274ba10b047e068  memory_vault_v026_alpha3_synthetic_ab.py
f6f3a6e337b14cfd320ed2e825f6bd73f20e3e55a9a539d7aa86c5702f050e26  memory_vault_v026_alpha3_synthetic_ab.result.json
```
