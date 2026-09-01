# Memory Vault v0.26.0-alpha.1 synthetic A/B artifact

Reproduction artifact for
[TimeToBuildBob/TimeToBuildBob#7](https://github.com/TimeToBuildBob/TimeToBuildBob/issues/7).
It contains synthetic data only. It does not contain identities, encryption keys,
invitations, credentials, private history, or the disposable runtime state.

## Pinned input

- Review ZIP: `memory-vault-review-v0.26.0-alpha.1.zip`
- Review ZIP SHA-256:
  `e932ee19736e5a5977aabdfd70ee5f2800d73c2c0e7b7861f20d83b272c05199`
- Source commit: `1b0eae0fdf8b2c045f99b92858c72da61cf9e753`
- Runtime: Ubuntu 24.04.3 LTS, CPython 3.12.3
- Topology: Starlette `TestClient` loopback URLs, one authority, one relay, two endpoints

## Command

From an extracted Review ZIP and the hash-locked virtual environment described
in the release quickstart:

```sh
ROOT=/tmp/memory-vault-pilot-20260831
cd "$ROOT"
"$ROOT/network-env/bin/python" \
  memory_vault_v026_synthetic_ab.py \
  > memory_vault_v026_synthetic_ab.result.json
sha256sum \
  memory_vault_v026_synthetic_ab.py \
  memory_vault_v026_synthetic_ab.result.json
```

The script intentionally keeps generated identity/key/vault/relay state beneath
a temporary directory and prints only the key-free JSON summary committed here.
Its absolute `ROOT` constant matches the disposable evaluation path; change that
constant when reproducing elsewhere.

## Cancellation probe

- Query: `retry the fixture probe`
- Limit: `4` (the native Agent facade's fixed page limit)
- Original goal deliberately contains the query phrase.
- Superseding cancellation is deliberately different:
  `Synthetic cancellation: the retry goal is cancelled; do not execute it.`

Both low-level Vault views and the six-operation Agent facade were queried.
The Vault output records memory IDs, status, score/order, and relations. The
Agent facade omits status/score/relations, so those fields are `null`/empty in
its output by design.

Observed in this rerun:

- ordinary Vault recall ranked the superseded goal first (`score_milli=3194`);
- Vault `handoff` returned the same superseded goal first;
- the current cancellation decision ranked second (`score_milli=1118`);
- Agent ordinary recall and `handoff:true` both surfaced the old goal first.

This sharpens the earlier report: structural handoff does filter superseded
records from its *structural additions*, but it then combines those additions
with unfiltered semantic hits. Therefore `handoff:true` does **not** exclude the
old goal when semantic relevance ranks it highly.

## Hashes

```txt
d529519bef484d463b336ed0c3308ad5b2edbe1af0a449b9342a06354924fb1b  memory_vault_v026_synthetic_ab.py
183da01ec773be8a6013717fd2efcc99458d310874a5b5cac3737a864c0f5fcc  memory_vault_v026_synthetic_ab.result.json
```
