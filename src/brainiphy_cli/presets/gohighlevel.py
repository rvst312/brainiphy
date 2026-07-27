#!/usr/bin/env python3
"""GoHighLevel / LeadConnector (API v2) connector.

Installed by `brain new-connector <project> <name> --preset gohighlevel`.
Complete as generated — the only thing to fill in is which sub-account to read
and where the token lives, both of which the CLI substitutes for you.

Scopes: a Private Integration Token (pit-…) carries only the scopes ticked when
it was created, and nothing in the API reports which those are. Every object is
therefore attempted and the ones that come back 401/403 are listed as "no
scope" and skipped. Run with --probe first to see the access matrix; widen the
token in GHL and the matching collector starts working on the next sync with no
change to this file.
"""
from __future__ import annotations

import sys

from brainiphy_cli import collect
from brainiphy_cli.collect import Collector, kv_block
from brainiphy_cli.httpclient import HttpClient

SOURCE_SYSTEM = "gohighlevel"

# The sub-account to read. Visible in the GHL URL:
# app.gohighlevel.com/v2/location/<LOCATION_ID>/dashboard
LOCATION_ID = "REPLACE_ME"

# Keychain item holding the pit-… token.
SECRET_ITEM = "REPLACE_ME_SECRET"

# The Version header is mandatory on v2 — without it requests are rejected in a
# way that does not mention the header.
client = HttpClient(
    "https://services.leadconnectorhq.com",
    secret_item=SECRET_ITEM,
    extra_headers={"Version": "2021-07-28"},
)

# Opportunities arrive with bare pipeline/stage ids, and an id is useless in a
# graph: "what is sitting in Awaiting payment" is the question people ask.
# collect_pipelines fills these in, and COLLECTORS runs it first. If the token
# has no pipelines scope they stay empty and the raw ids are kept.
PIPELINE_NAMES: dict[str, str] = {}
STAGE_NAMES: dict[str, str] = {}


def collect_location() -> list[dict]:
    loc = client.get(f"/locations/{LOCATION_ID}").get("location") or {}
    if not loc:
        return []
    return [{
        "id": loc.get("id", LOCATION_ID),
        "title": f"Location: {loc.get('name', LOCATION_ID)}",
        "body": kv_block(loc, ["name", "address", "city", "state", "country", "postalCode",
                               "website", "timezone", "email", "phone", "business"]),
        "record_type": "location",
    }]


def collect_custom_fields() -> list[dict]:
    rows = client.get(f"/locations/{LOCATION_ID}/customFields").get("customFields") or []
    if not rows:
        return []
    body = "\n".join(f"- **{f.get('name')}** (`{f.get('fieldKey')}`) — {f.get('dataType')}"
                     for f in rows)
    return [{"id": "custom-fields", "title": "Custom fields defined in this account",
             "body": body, "record_type": "schema"}]


def collect_tags() -> list[dict]:
    rows = client.get(f"/locations/{LOCATION_ID}/tags").get("tags") or []
    if not rows:
        return []
    body = "\n".join(f"- {t.get('name')}" for t in rows)
    return [{"id": "tags", "title": "Tags defined in this account",
             "body": body, "record_type": "schema"}]


def collect_users() -> list[dict]:
    rows = client.get("/users/", {"locationId": LOCATION_ID}).get("users") or []
    out = []
    for u in rows:
        name = u.get("name") or " ".join(filter(None, [u.get("firstName"), u.get("lastName")]))
        out.append({"id": f"user-{u['id']}", "title": f"User: {name}",
                    "body": kv_block(u, ["email", "phone", "role", "type", "permissions"]),
                    "record_type": "user", "ghl_id": u["id"]})
    return out


def collect_contacts() -> list[dict]:
    rows = client.paginate("/contacts/", {"locationId": LOCATION_ID}, "contacts")
    out = []
    for c in rows:
        name = (c.get("contactName")
                or " ".join(filter(None, [c.get("firstName"), c.get("lastName")]))
                or c.get("email") or c["id"])
        out.append({
            "id": f"contact-{c['id']}",
            "title": f"Contact: {name}",
            "body": kv_block(c, ["email", "phone", "companyName", "address1", "city", "state",
                                 "country", "source", "type", "tags", "dateAdded",
                                 "dateUpdated", "customFields", "assignedTo"]),
            "record_type": "contact",
            "ghl_id": c["id"],
        })
    return out


def collect_pipelines() -> list[dict]:
    rows = client.get("/opportunities/pipelines", {"locationId": LOCATION_ID}).get("pipelines") or []
    out = []
    for p in rows:
        PIPELINE_NAMES[p["id"]] = p.get("name") or p["id"]
        stages = p.get("stages") or []
        for stage in stages:
            STAGE_NAMES[stage["id"]] = stage.get("name") or stage["id"]
        listed = "\n".join(f"  {i + 1}. {s.get('name')}" for i, s in enumerate(stages))
        out.append({"id": f"pipeline-{p['id']}", "title": f"Pipeline: {p.get('name')}",
                    "body": f"Stages:\n{listed}", "record_type": "pipeline", "ghl_id": p["id"]})
    return out


def collect_opportunities() -> list[dict]:
    # snake_case location_id here, camelCase everywhere else. Not a typo.
    rows = client.paginate("/opportunities/search", {"location_id": LOCATION_ID}, "opportunities")
    out = []
    for o in rows:
        pipeline = PIPELINE_NAMES.get(o.get("pipelineId"), o.get("pipelineId"))
        stage = STAGE_NAMES.get(o.get("pipelineStageId"), o.get("pipelineStageId"))
        lead = kv_block(
            {"contact": (o.get("contact") or {}).get("name"), "pipeline": pipeline, "stage": stage},
            ["contact", "pipeline", "stage"],
        )
        rest = kv_block(o, ["status", "monetaryValue", "source", "assignedTo",
                            "createdAt", "updatedAt", "lastStatusChangeAt"])
        out.append({
            "id": f"opportunity-{o['id']}",
            "title": f"Opportunity: {o.get('name')}",
            "body": "\n".join(filter(None, [lead, rest])),
            "record_type": "opportunity",
            "ghl_id": o["id"],
            # Also as frontmatter, so the stage is filterable without parsing prose.
            "pipeline": pipeline,
            "stage": stage,
        })
    return out


def collect_conversations() -> list[dict]:
    rows = client.paginate("/conversations/search", {"locationId": LOCATION_ID}, "conversations")
    out = []
    for i, conv in enumerate(rows):
        header = kv_block(conv, ["contactName", "email", "phone", "type", "lastMessageBody",
                                 "lastMessageDate", "unreadCount"])
        transcript = ""
        # The message bodies are the part worth indexing, but they cost one
        # request per conversation. MAX_TRANSCRIPTS caps that; the collector's
        # note in COLLECTORS says so rather than silently truncating.
        if i < MAX_TRANSCRIPTS:
            try:
                payload = client.get(f"/conversations/{conv['id']}/messages", {"limit": 100})
                msgs = (payload.get("messages") or {}).get("messages") or []
                transcript = "\n\n".join(
                    f"**{m.get('direction', '?')}** ({m.get('dateAdded', '')}): {m.get('body', '')}"
                    for m in reversed(msgs) if m.get("body")
                )
            except Exception:  # noqa: BLE001 — a missing transcript is not worth losing the thread
                transcript = "_(messages not readable with this token)_"
        out.append({
            "id": f"conversation-{conv['id']}",
            "title": f"Conversation: {conv.get('contactName') or conv['id']}",
            "body": header + (f"\n\n## Transcript\n\n{transcript}" if transcript else ""),
            "record_type": "conversation",
            "ghl_id": conv["id"],
        })
    return out


def collect_calendars() -> list[dict]:
    rows = client.get("/calendars/", {"locationId": LOCATION_ID}).get("calendars") or []
    return [{
        "id": f"calendar-{c['id']}",
        "title": f"Calendar: {c.get('name')}",
        "body": kv_block(c, ["description", "calendarType", "slotDuration", "slotInterval",
                             "timezone", "isActive", "teamMembers"]),
        "record_type": "calendar",
        "ghl_id": c["id"],
    } for c in rows]


def collect_forms() -> list[dict]:
    rows = client.paginate("/forms/", {"locationId": LOCATION_ID}, "forms")
    return [{
        "id": f"form-{f['id']}",
        "title": f"Form: {f.get('name')}",
        "body": kv_block(f, ["description", "createdAt", "updatedAt"]),
        "record_type": "form",
        "ghl_id": f["id"],
    } for f in rows]


MAX_TRANSCRIPTS = 200

# Order matters: pipelines before opportunities, so stage ids resolve to names.
COLLECTORS = [
    Collector("location", "location", collect_location),
    Collector("custom-fields", "schema", collect_custom_fields),
    Collector("tags", "schema", collect_tags),
    Collector("users", "users", collect_users),
    Collector("contacts", "contacts", collect_contacts),
    Collector("pipelines", "pipelines", collect_pipelines),
    Collector("opportunities", "opportunities", collect_opportunities),
    Collector("conversations", "conversations", collect_conversations,
              note=f"transcripts for the first {MAX_TRANSCRIPTS}"),
    Collector("calendars", "calendars", collect_calendars),
    Collector("forms", "forms", collect_forms),
]


def main() -> int:
    return collect.run(SOURCE_SYSTEM, COLLECTORS, context=f"location {LOCATION_ID}")


if __name__ == "__main__":
    sys.exit(main())
