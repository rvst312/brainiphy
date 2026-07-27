"""Ready-to-run connectors for systems brainiphy already knows.

A preset is the third thing `brain new-connector` can produce, alongside the
mirror template (a local folder) and the API template (a stub to fill in). The
difference is that a preset is *finished*: the endpoints, the pagination style,
the record shape and the quirks of one specific vendor are already written
down, so installing it costs a couple of answers rather than an afternoon of
reading API docs.

Adding one:
  1. Drop `<name>.py` in this folder — a complete connector, with each value
     the installer must supply written as `CONST = "REPLACE_ME…"` at the top.
  2. Register it in PRESETS below, declaring those constants as Variables.
The file is copied as text and the constants substituted, never imported, so a
preset holding placeholders is fine and does not need to be runnable as-is.

Presets stay honest about scope: a vendor's token usually cannot read
everything, so a preset should attempt each object and let collect.run() report
what came back rather than assuming access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PRESET_DIR = Path(__file__).resolve().parent


@dataclass
class Variable:
    """A constant the installer has to fill in before the connector will run."""

    name: str            # the constant in the template, e.g. LOCATION_ID
    prompt: str          # what to ask a human
    example: str = ""    # shown as a hint, never used as a default
    where: str = ""      # where to find the value


@dataclass
class Preset:
    name: str
    title: str
    description: str
    module: str                                  # filename in this folder
    variables: list[Variable] = field(default_factory=list)
    needs_secret: bool = True
    secret_prompt: str = "API token"
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        return (PRESET_DIR / self.module).read_text(encoding="utf-8")


PRESETS: dict[str, Preset] = {
    "gohighlevel": Preset(
        name="gohighlevel",
        title="GoHighLevel / LeadConnector",
        description="Contacts, opportunities, pipelines, conversations, calendars, users and forms "
                    "from one GHL sub-account (API v2).",
        module="gohighlevel.py",
        variables=[
            Variable(
                name="LOCATION_ID",
                prompt="GHL sub-account (location) ID",
                # A shape, not a real account: this file ships to every client
                # project, so no actual tenant id belongs in it.
                example="ve9EPM428h8vShlRW1KT",
                where="in the URL while inside the sub-account: "
                      "app.gohighlevel.com/v2/location/<THIS>/dashboard",
            ),
        ],
        secret_prompt="GHL Private Integration Token (pit-…)",
        notes=[
            "A pit- token only carries the scopes ticked when it was created, and no endpoint "
            "lists them. Run the connector with --probe to see what it can actually read.",
            "Objects the token cannot read are skipped, not fatal — widen the token in GHL and "
            "they start syncing on the next run with no code change.",
        ],
    ),
}


def get(name: str) -> Preset | None:
    return PRESETS.get(name)


def names() -> list[str]:
    return sorted(PRESETS)
