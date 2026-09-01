#!/usr/bin/env python3
"""Disposable Memory Vault A/B pilot for TimeToBuildBob/TimeToBuildBob#7.

Synthetic data only. Prints a key-free JSON summary. Private state stays
under /tmp/memory-vault-pilot-20260831/private-runtime/.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

ROOT = Path("/tmp/memory-vault-pilot-20260831/memory-vault-review-v0.26.0-alpha.1")
sys.path.insert(0, str(ROOT))

from memory_vault import canonical_bytes  # noqa: E402
from memory_vault_agent import Agent  # noqa: E402
from memory_vault_client import CONFIG_SCHEMA, ClientConfig  # noqa: E402
from memory_vault_network_control import create_authority_app, issue_invite, issue_roster  # noqa: E402
from memory_vault_network_crypto import EncryptionIdentity, document_sha256  # noqa: E402
from memory_vault_relay import create_app  # noqa: E402
from memory_vault_storage import atomic_write  # noqa: E402
from memory_vault_trust import Identity, TrustStore  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


class Transport:
    def __init__(self, clients):
        self.clients = clients

    def request(self, base, method, path, value=None):
        from memory_vault import MemoryError

        response = self.clients[base].request(
            method,
            path,
            content=None if value is None else canonical_bytes(value),
            headers={"content-type": "application/json"},
        )
        result = response.json()
        if response.status_code != 200:
            error = result.get("error", {})
            raise MemoryError(error if isinstance(error, str) else error.get("code", "network_unavailable"))
        return result


def remember(agent, **kwargs):
    result = agent.handle({"op": "remember", **kwargs})
    if not result.get("ok"):
        raise SystemExit(json.dumps({"failed": "remember", "result": result}, indent=2))
    return result["result"]


def require_ok(label, result):
    if not result.get("ok"):
        raise SystemExit(json.dumps({"failed": label, "result": result}, indent=2))
    return result["result"]


def vault_get(config, memory_id):
    return ClientConfig.load(config).vault().handle({"op": "get", "memory_id": memory_id})["result"]["record"]


def vault_status(config, memory_id):
    connection = ClientConfig.load(config).vault()._connect()
    try:
        from memory_vault import Vault

        return Vault._memory_status(connection, memory_id)
    finally:
        connection.close()


def count_memories(config):
    vault = ClientConfig.load(config).vault()
    connection = vault._connect()
    try:
        return int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    finally:
        connection.close()


def main() -> None:
    private = Path("/tmp/memory-vault-pilot-20260831/private-runtime")
    private.mkdir(parents=True, exist_ok=True)
    summary = {
        "version": "0.26.0-alpha.1",
        "source_commit": "1b0eae0fdf8b2c045f99b92858c72da61cf9e753",
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform},
        "topology": "1 authority + 1 relay + 2 endpoints (Starlette TestClient, loopback URLs)",
        "synthetic_only": True,
    }
    with tempfile.TemporaryDirectory(prefix="bob-mv-pilot-", dir=private) as temporary, ExitStack() as stack:
        root = Path(temporary).resolve()
        issuer = Identity.generate(root / "issuer.json")
        trust = TrustStore(root / "issuer-trust.json")
        trust.add(issuer.public_descriptor())
        identities, encryption, configs = [], [], []
        for name in ("agent-a", "agent-b"):
            member_root = root / name
            identity = Identity.generate(member_root / "identity.json")
            key = EncryptionIdentity.generate()
            key.save(member_root / "encryption.json")
            identities.append(identity)
            encryption.append(key)
            config = member_root / "client.json"
            atomic_write(
                config,
                canonical_bytes(
                    {
                        "schema_version": CONFIG_SCHEMA,
                        "vault_path": str(member_root / "memory.sqlite3"),
                        "capture_visible_turns": False,
                        "identity_path": str(member_root / "identity.json"),
                        "trust_path": str(member_root / "trust.json"),
                    }
                ),
                replace=False,
            )
            configs.append(config)
        for config in configs:
            record_trust = TrustStore(config.parent / "trust.json")
            for identity in identities:
                record_trust.add(identity.public_descriptor())
        now = int(time.time())
        members = [
            {
                "signing_key": identity.public_descriptor(),
                "encryption_key": key.public_descriptor(),
                "status": "active",
                "scope": ["receive", "send"],
            }
            for identity, key in zip(identities, encryption)
        ]
        network = "bob-synthetic-pilot"
        roster = issue_roster(
            issuer,
            network_id=network,
            version=1,
            previous_sha256="0" * 64,
            members=members,
            issued_at=now,
            expires_at=now + 300,
        )
        roster_path = root / "roster.json"
        atomic_write(roster_path, canonical_bytes(roster), replace=False)
        authority = root / "authority.json"
        atomic_write(
            authority,
            canonical_bytes(
                {
                    "schema_version": "memory-vault-network-authority-config/v1",
                    "network_id": network,
                    "identity_path": str(root / "issuer.json"),
                    "trust_store_path": str(root / "issuer-trust.json"),
                    "roster_path": str(roster_path),
                }
            ),
            replace=False,
        )
        authority_url = "http://127.0.0.1:8767"
        relay_url = "http://127.0.0.1:8765"
        clients = {authority_url: stack.enter_context(TestClient(create_authority_app(authority)))}
        relay_config = root / "relay.json"
        atomic_write(
            relay_config,
            canonical_bytes(
                {
                    "schema_version": "memory-vault-relay-config/v1",
                    "network_id": network,
                    "issuer_public_key": issuer.public_descriptor(),
                    "roster_path": str(roster_path),
                    "state_directory": str(root / "relay-state"),
                    "base_url": relay_url,
                    "init_member_key_ids": [identities[0].key_id],
                }
            ),
            replace=False,
        )
        clients[relay_url] = stack.enter_context(TestClient(create_app(relay_config)))
        transport = Transport(clients)
        network_configs = []
        for config in configs:
            path = config.parent / "network.json"
            atomic_write(
                path,
                canonical_bytes(
                    {
                        "schema_version": "memory-vault-network-client/v1",
                        "network_id": network,
                        "client_config_path": str(config),
                        "state_directory": str(config.parent / "network-state"),
                        "encryption_key_path": str(config.parent / "encryption.json"),
                        "issuer_public_key": issuer.public_descriptor(),
                        "relays": [relay_url],
                        "authority_url": authority_url,
                    }
                ),
                replace=False,
            )
            network_configs.append(path)
        agent_a = Agent(configs[0], network_configs[0], transport=transport)
        agent_b = Agent(configs[1], network_configs[1], transport=transport)
        invite = issue_invite(
            issuer,
            network_id=network,
            invite_id="bob-synthetic-invite",
            candidate_signing_key=identities[1].public_descriptor(),
            candidate_encryption_key=encryption[1].public_descriptor(),
            scope=["receive", "send"],
            handoff_sha256=hashlib.sha256(b"").hexdigest(),
            roster_sha256=document_sha256(roster),
            issued_at=now,
            expires_at=now + 300,
        )
        require_ok(
            "connect-b",
            agent_b.handle(
                {
                    "op": "connect",
                    "invitation": {"invite": invite, "roster": roster},
                    "request_id": "req_bob_join",
                }
            ),
        )
        require_ok("connect-a", agent_a.handle({"op": "connect", "request_id": "req_bob_join_a"}))

        decision = remember(
            agent_a,
            request_id="req_bob_decision",
            kind="decision",
            text="Synthetic decision: continue only from verified selected evidence, never from a relay ack.",
        )
        constraint = remember(
            agent_a,
            request_id="req_bob_constraint",
            kind="fact",
            text="Synthetic constraint: loopback-only, no private history, no paid infra.",
            relations=[{"type": "related_to", "target": decision["memory_id"]}],
        )
        failure = remember(
            agent_a,
            request_id="req_bob_failure",
            kind="observation",
            text=(
                "Synthetic failed approach: fixture service was stopped, so a live HTTP probe "
                "returned connection refused. Cause is environmental, recorded_at is historical."
            ),
            relations=[{"type": "derived_from", "target": constraint["memory_id"]}],
        )
        goal = remember(
            agent_a,
            request_id="req_bob_goal",
            kind="goal",
            text="Synthetic goal: retry the fixture probe after the service is confirmed running.",
            relations=[{"type": "derived_from", "target": failure["memory_id"]}],
        )
        ids = {
            "decision": decision["memory_id"],
            "constraint": constraint["memory_id"],
            "failure": failure["memory_id"],
            "goal": goal["memory_id"],
        }
        send = {
            "op": "send",
            "request_id": "req_bob_delivery",
            "recipients": [identities[1].key_id],
            "text": "Synthetic selected evidence packet",
            "memory_ids": list(ids.values()),
        }
        first_send = require_ok("send-1", agent_a.handle(send))
        received = require_ok("receive-1", agent_b.handle({"op": "receive"}))
        before_restart_count = count_memories(configs[1])

        # Restart B: new Agent object, same durable vault/network state.
        agent_b_restarted = Agent(configs[1], network_configs[1], transport=transport)
        recalled = require_ok(
            "recall-after-restart",
            agent_b_restarted.handle({"op": "recall", "query": "fixture service failed approach", "handoff": True}),
        )
        recalled_ids = [hit["memory_id"] for hit in recalled.get("hits", [])]
        recalled_kinds = {hit["memory_id"]: hit.get("kind") for hit in recalled.get("hits", [])}

        duplicate_send = require_ok("send-duplicate", agent_a.handle(send))
        duplicate_receive = require_ok("receive-duplicate", agent_b_restarted.handle({"op": "receive"}))
        after_duplicate_count = count_memories(configs[1])

        changed = remember(
            agent_a,
            request_id="req_bob_changed_failure",
            kind="observation",
            text=(
                "Synthetic revalidation: fixture service is now running on loopback. "
                "The previous connection-refused observation remains historical evidence, not a veto."
            ),
            relations=[{"type": "supersedes", "target": failure["memory_id"]}],
        )
        cancel = remember(
            agent_a,
            request_id="req_bob_cancel",
            kind="decision",
            text="Synthetic cancellation: the retry goal is cancelled; do not execute it.",
            relations=[{"type": "supersedes", "target": goal["memory_id"]}],
        )
        followup = {
            "op": "send",
            "request_id": "req_bob_followup",
            "recipients": [identities[1].key_id],
            "text": "Synthetic changed-condition and cancellation packet",
            "memory_ids": [changed["memory_id"], cancel["memory_id"]],
        }
        require_ok("send-followup", agent_a.handle(followup))
        require_ok("receive-followup", agent_b_restarted.handle({"op": "receive"}))

        failure_after = vault_get(configs[1], ids["failure"])
        goal_after = vault_get(configs[1], ids["goal"])
        plaintext_leaks = []
        for stored in (root / "relay-state").rglob("*"):
            if stored.is_file() and b"Synthetic selected evidence packet" in stored.read_bytes():
                plaintext_leaks.append(str(stored.relative_to(root)))

        b_failure_status = vault_status(configs[1], ids["failure"])
        b_goal_status = vault_status(configs[1], ids["goal"])
        b_cancel_status = vault_status(configs[1], cancel["memory_id"])
        b_changed_status = vault_status(configs[1], changed["memory_id"])
        recall_query = "retry the fixture probe"
        recall_limit = 4  # Agent facade's fixed result-page limit.
        vault_b = ClientConfig.load(configs[1]).vault()
        ordinary_ranked = require_ok(
            "ordinary-vault-recall-after-cancel",
            vault_b.handle({"op": "recall", "query": recall_query, "limit": recall_limit}),
        )
        handoff_ranked = require_ok(
            "vault-handoff-after-cancel",
            vault_b.handle({"op": "handoff", "query": recall_query, "limit": recall_limit}),
        )
        ordinary_recall = require_ok(
            "ordinary-agent-recall-after-cancel",
            agent_b_restarted.handle({"op": "recall", "query": recall_query}),
        )
        handoff = require_ok(
            "agent-handoff-after-cancel",
            agent_b_restarted.handle({"op": "recall", "query": recall_query, "handoff": True}),
        )

        def public_hits(result):
            return [
                {
                    "memory_id": hit.get("memory_id"),
                    "kind": hit.get("kind"),
                    "status": hit.get("status"),
                    "relations": hit.get("relations", []),
                    # The Agent facade intentionally does not expose scores.
                    "score_milli": hit.get("score_milli"),
                }
                for hit in result.get("hits", [])
            ]

        ordinary_ranked_hits = public_hits(ordinary_ranked)
        handoff_ranked_hits = public_hits(handoff_ranked)
        ordinary_hits = public_hits(ordinary_recall)
        handoff_hits = public_hits(handoff)

        summary.update(
            {
                "step1": {
                    "a_saved_kinds": ["decision", "fact", "observation", "goal"],
                    "send_state": first_send.get("state"),
                    "stored_nodes": first_send.get("stored_nodes"),
                    "endpoint_validated": first_send.get("endpoint_validated"),
                    "b_received_messages": len(received.get("messages", [])),
                    "b_admission": [msg.get("share", {}).get("admission") for msg in received.get("messages", [])],
                    "execution_eligible": received.get("authority", {}).get("execution_eligible")
                    if "authority" in received
                    else None,
                    "b_memory_count_before_restart": before_restart_count,
                    "b_has_all_selected_ids": all(
                        vault_get(configs[1], memory_id)["memory_id"] == memory_id for memory_id in ids.values()
                    ),
                    "restart_recall_hit_count": len(recalled_ids),
                    "restart_recall_includes_failure": ids["failure"] in recalled_ids,
                    "restart_recall_includes_decision": ids["decision"] in recalled_ids,
                    "restart_recall_kinds": sorted(set(recalled_kinds.values())),
                },
                "step2": {
                    "duplicate_send_state": duplicate_send.get("state"),
                    "duplicate_send_stored_nodes": duplicate_send.get("stored_nodes"),
                    "duplicate_receive_message_count": len(duplicate_receive.get("messages", [])),
                    "b_memory_count_after_duplicate": after_duplicate_count,
                    "no_duplicate_canonical_memories": after_duplicate_count == before_restart_count,
                    "changed_failure_status_on_b": b_failure_status,
                    "changed_observation_status_on_b": b_changed_status,
                    "original_failure_text_preserved": "connection refused" in failure_after["text"],
                },
                "step3": {
                    "cancelled_goal_status_on_b": b_goal_status,
                    "cancellation_decision_status_on_b": b_cancel_status,
                    "recall_query": recall_query,
                    "recall_limit": recall_limit,
                    "vault_ordinary_ranked_hits": ordinary_ranked_hits,
                    "vault_handoff_ranked_hits": handoff_ranked_hits,
                    "agent_ordinary_hits": ordinary_hits,
                    "agent_handoff_hits": handoff_hits,
                    "ordinary_recall_surfaces_cancelled_goal": any(
                        hit.get("memory_id") == ids["goal"] for hit in ordinary_recall.get("hits", [])
                    ),
                    "handoff_surfaces_cancelled_goal": any(
                        hit.get("memory_id") == ids["goal"] for hit in handoff.get("hits", [])
                    ),
                    "relay_plaintext_leaks": plaintext_leaks,
                    "recipient_save_separate_from_relay": after_duplicate_count >= 4 and not plaintext_leaks,
                    "memory_does_not_authorize_execution": received.get("authority", {}).get("execution_eligible")
                    is not True,
                    "original_goal_text_preserved": "retry the fixture probe" in goal_after["text"],
                },
            }
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
