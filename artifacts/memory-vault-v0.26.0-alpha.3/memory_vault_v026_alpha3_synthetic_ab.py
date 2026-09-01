#!/usr/bin/env python3
"""Disposable Memory Vault A/B follow-up for TimeToBuildBob/TimeToBuildBob#7.

Re-runs the alpha.1 negative case against v0.26.0-alpha.3: current cancellation
vs superseded goal ranking in ordinary recall, handoff, the six-operation Agent
facade, and pagination.

Synthetic data only. Prints a key-free JSON summary. Private state stays
under /tmp/memory-vault-pilot-20260901/private-runtime/.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(
    "/tmp/memory-vault-pilot-20260901/memory-vault-review-v0.26.0-alpha.3/"
    "memory-vault-review-v0.26.0-alpha.3"
)
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


def public_hits(result):
    return [
        {
            "memory_id": hit.get("memory_id"),
            "kind": hit.get("kind"),
            "status": hit.get("status"),
            "relations": hit.get("relations", []),
            "score_milli": hit.get("score_milli"),
        }
        for hit in result.get("hits", [])
    ]


def ranks_current_before_superseded(hits, current_id, superseded_id):
    ids = [hit.get("memory_id") for hit in hits]
    if current_id not in ids or superseded_id not in ids:
        return False
    return ids.index(current_id) < ids.index(superseded_id)


def first_id(hits):
    return hits[0]["memory_id"] if hits else None


def main() -> None:
    private = Path("/tmp/memory-vault-pilot-20260901/private-runtime")
    private.mkdir(parents=True, exist_ok=True)
    summary = {
        "version": "0.26.0-alpha.3",
        "source_commit": "d657d8381326693673426e66373d47e80f73be40",
        "review_zip_sha256": "fccc8d4d5aead869e42de3ec744fcdafcf224fd19a63e7e378eed3c98960251c",
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform},
        "topology": "1 authority + 1 relay + 2 endpoints (Starlette TestClient, loopback URLs)",
        "synthetic_only": True,
        "compared_against": "v0.26.0-alpha.1 negative case from TimeToBuildBob/TimeToBuildBob#8",
    }
    with tempfile.TemporaryDirectory(prefix="bob-mv-pilot-alpha3-", dir=private) as temporary, ExitStack() as stack:
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
        network = "bob-synthetic-pilot-alpha3"
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
            invite_id="bob-synthetic-invite-alpha3",
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
        recall_limit = 4
        vault_b = ClientConfig.load(configs[1]).vault()
        ordinary_ranked = require_ok(
            "ordinary-vault-recall-after-cancel",
            vault_b.handle({"op": "recall", "query": recall_query, "limit": recall_limit}),
        )
        handoff_ranked = require_ok(
            "vault-handoff-after-cancel",
            vault_b.handle({"op": "handoff", "query": recall_query, "limit": recall_limit}),
        )
        ordinary_limit1 = require_ok(
            "ordinary-vault-recall-limit-1",
            vault_b.handle({"op": "recall", "query": recall_query, "limit": 1}),
        )
        handoff_limit1 = require_ok(
            "vault-handoff-limit-1",
            vault_b.handle({"op": "handoff", "query": recall_query, "limit": 1}),
        )
        ordinary_recall = require_ok(
            "ordinary-agent-recall-after-cancel",
            agent_b_restarted.handle({"op": "recall", "query": recall_query}),
        )
        handoff = require_ok(
            "agent-handoff-after-cancel",
            agent_b_restarted.handle({"op": "recall", "query": recall_query, "handoff": True}),
        )

        ordinary_ranked_hits = public_hits(ordinary_ranked)
        handoff_ranked_hits = public_hits(handoff_ranked)
        ordinary_limit1_hits = public_hits(ordinary_limit1)
        handoff_limit1_hits = public_hits(handoff_limit1)
        ordinary_hits = public_hits(ordinary_recall)
        handoff_hits = public_hits(handoff)

        agent_page2 = None
        agent_page2_hits = []
        agent_cursor = ordinary_recall.get("next_cursor")
        if agent_cursor:
            agent_page2 = require_ok(
                "ordinary-agent-recall-page-2",
                agent_b_restarted.handle({"op": "recall", "cursor": agent_cursor}),
            )
            agent_page2_hits = public_hits(agent_page2)

        handoff_page2 = None
        handoff_page2_hits = []
        handoff_cursor = handoff.get("next_cursor")
        if handoff_cursor:
            handoff_page2 = require_ok(
                "agent-handoff-page-2",
                agent_b_restarted.handle({"op": "recall", "cursor": handoff_cursor}),
            )
            handoff_page2_hits = public_hits(handoff_page2)

        cancel_id = cancel["memory_id"]
        goal_id = ids["goal"]
        page1_ids = [hit["memory_id"] for hit in ordinary_hits]
        page2_ids = [hit["memory_id"] for hit in agent_page2_hits]
        overlap = sorted(set(page1_ids) & set(page2_ids))

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
                    "cancellation_memory_id": cancel_id,
                    "superseded_goal_memory_id": goal_id,
                    "recall_query": recall_query,
                    "recall_limit": recall_limit,
                    "vault_ordinary_ranked_hits": ordinary_ranked_hits,
                    "vault_handoff_ranked_hits": handoff_ranked_hits,
                    "vault_ordinary_limit1_hits": ordinary_limit1_hits,
                    "vault_handoff_limit1_hits": handoff_limit1_hits,
                    "agent_ordinary_hits": ordinary_hits,
                    "agent_handoff_hits": handoff_hits,
                    "agent_ordinary_next_cursor_present": bool(agent_cursor),
                    "agent_ordinary_page2_hits": agent_page2_hits,
                    "agent_handoff_next_cursor_present": bool(handoff_cursor),
                    "agent_handoff_page2_hits": handoff_page2_hits,
                    "ordinary_recall_surfaces_cancelled_goal": any(
                        hit.get("memory_id") == goal_id for hit in ordinary_recall.get("hits", [])
                    ),
                    "handoff_surfaces_cancelled_goal": any(
                        hit.get("memory_id") == goal_id for hit in handoff.get("hits", [])
                    ),
                    "vault_ordinary_current_before_superseded": ranks_current_before_superseded(
                        ordinary_ranked_hits, cancel_id, goal_id
                    ),
                    "vault_handoff_current_before_superseded": ranks_current_before_superseded(
                        handoff_ranked_hits, cancel_id, goal_id
                    ),
                    "agent_ordinary_current_before_superseded": ranks_current_before_superseded(
                        ordinary_hits, cancel_id, goal_id
                    ),
                    "agent_handoff_current_before_superseded": ranks_current_before_superseded(
                        handoff_hits, cancel_id, goal_id
                    ),
                    "vault_ordinary_limit1_is_cancellation": first_id(ordinary_limit1_hits) == cancel_id,
                    "vault_handoff_limit1_is_cancellation": first_id(handoff_limit1_hits) == cancel_id,
                    "agent_ordinary_first_is_cancellation": first_id(ordinary_hits) == cancel_id,
                    "agent_handoff_first_is_cancellation": first_id(handoff_hits) == cancel_id,
                    "pagination_page2_does_not_repeat_page1_ids": not overlap,
                    "pagination_page2_ids": page2_ids,
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
