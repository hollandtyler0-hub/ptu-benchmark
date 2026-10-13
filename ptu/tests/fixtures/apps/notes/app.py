"""Small source-free application exercising kernel boundaries."""

from ptu.apps.base import Actor, AppError, AppSpec
from ptu.common.registry import ResourceUse, ToolSpec


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _authorize(actor, tool, arguments, state):
    if actor.role not in {"editor", "viewer"}:
        raise AppError("unknown_role", "This role is unavailable.", "permission")
    if actor.role == "viewer" and tool != "notes.get_note":
        raise AppError("insufficient_permissions", "Editors can change notes.", "permission")


def _get(args, state, clock, ids):
    note = state["items"].get(args["id"])
    if note is None:
        raise AppError("note_not_found", "The note was not found.", "not_found", "id")
    return dict(note)


def _create(args, state, clock, ids):
    note_id = ids.next("note")
    state["items"][note_id] = {"id": note_id, "title": args["title"]}
    if args["title"] == "blocked":
        raise AppError("title_reserved", "This title is reserved.", "validation", "title")
    effects = {"stub_effects": [{"endpoint": "fixture"}]}
    if args["title"] == "push":
        effects["push_effects"] = [{"repo": "fixture/notes", "ref": "refs/heads/main", "status": "accepted"}]
    return {"id": note_id, "_effects": effects}


def _delete(args, state, clock, ids):
    if not args.get("confirm"):
        return {"requires_confirmation": True, "dialog": "Delete this note?"}
    if args["id"] not in state["items"]:
        raise AppError("note_not_found", "The note was not found.", "not_found", "id")
    del state["items"][args["id"]]
    return {"deleted": True}


def _event_add_note(args, state, clock, ids):
    note_id = args["id"]
    state["items"][note_id] = {"id": note_id, "title": "Scheduled note"}
    return [ResourceUse("notes:note/" + note_id, "write")]


def _spec(name, effect, handler, properties, required, resources_fn, **extra):
    return ToolSpec(name="notes." + name, effect=effect, privilege=False,
                    idempotent=effect == "read", annotations={"readOnlyHint": effect == "read",
                    "destructiveHint": effect == "destructive", "idempotentHint": effect == "read",
                    "openWorldHint": False}, input_schema=_schema(properties, required),
                    auth_args=(), resources_fn=resources_fn, handler=handler,
                    surface="notes", ui_path="Notes > " + name.replace("_", " "), **extra)


def _tools(actor, world):
    string = {"type": "string"}
    return [
        _spec("get_note", "read", _get, {"id": string}, ["id"],
              lambda a, s: [ResourceUse("notes:note/" + a["id"], "read")]),
        _spec("create_note", "write", _create, {"title": string}, ["title"],
              lambda a, s: [], effects_fn=lambda a, r, s: [ResourceUse("notes:note/" + r["id"], "write")]),
        _spec("delete_note", "destructive", _delete,
              {"id": string, "confirm": {"type": "boolean"}}, ["id"],
              lambda a, s: [ResourceUse("notes:note/" + a["id"], "write")], dialog=True),
    ]


APP = AppSpec(
    id="notes", reference_product="Invented Notes", fidelity_version="0.1",
    surfaces=("notes",), report_unit=lambda ref: ref,
    tool_specs=_tools, authorize=_authorize,
    refs_for_path=lambda path: "notes:note/" + path[1] if len(path) > 1 else "notes:notes/all",
    ancestors=lambda ref, state=None: ["notes:folder/all"] if ref.startswith("notes:note/") else [],
    bookkeeping=lambda path, key: key == "next_note_id",
    created_ref=lambda tool, args, result: "notes:note/" + result["id"] if tool == "notes.create_note" and result else None,
    external_check=None, event_ops={"add_note": _event_add_note},
    unread=lambda state, actor: {"notes": len(state["items"])},
)
