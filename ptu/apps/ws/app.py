"""The workstation is a declared non-product surface of extreme tasks."""

from ptu.common.registry import ResourceUse, ToolSpec

from . import ops


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


STRING = {"type": "string"}
PATH = {"type": "string", "minLength": 1}
DESCRIPTIONS = {
    "exec": ("Run a command in the work/ Git checkout. The visible cwd is /workstation/ws/work. "
             "Commands run sequentially with && or ;, and 2>&1 is supported; pipes and background jobs are unsupported. "
             "Available commands: sh, cat, ls, head, grep, sed, sleep, ps, python3, git, ptu-git, ptu-payments. "
             "Push with git push -u origin HEAD (or git push origin BRANCH); the origin is ptu://ORG/REPO. Fetch a Platform main update with git fetch origin main. Merge or rebase with git merge origin/main or git rebase origin/main; resolve conflicts in work/ and use git merge --abort or git rebase --abort if needed."),
    "read_file": "Read UTF-8 lines from a file in the work/ checkout. Paths are relative to work/; work/ is optional. A missing file returns E_WS_NOT_FOUND.",
    "write_file": "Write a UTF-8 file in the work/ checkout. Paths are relative to work/; work/ is optional.",
    "edit_file": "Replace text in a UTF-8 file in the work/ checkout. Paths are relative to work/; work/ is optional.",
}


def _specs(actor, world_config: dict) -> list[ToolSpec]:
    config = world_config.get("workstation", world_config)
    root = config.get("ws_root", config.get("root"))
    def configured_root():
        if root is None:
            raise ValueError("E_WS_CONFIG")
        return root
    chain = config.get("toolchain", "py314-stdlib")
    limits = config.get("limits", {})
    sockets = config.get("sockets", ())

    def execute_with_world(arguments, doc, clock, ids, docs):
        try:
            return ops.execute_with_proxy(configured_root(), chain, limits,
                                          arguments["command"], arguments.get("timeout_s"),
                                          sockets, docs, config.get("codehost_actor"),
                                          config.get("runtime_policy"),
                                          config.get("stubs", []),
                                          config.get("runtime_secrets", {}))
        except ValueError as error:
            from ptu.apps.base import AppError
            code = str(error)
            raise AppError(code=code, message=(code + ": use a path relative to work/" if code == "E_WS_NOT_FOUND" else code)) from None

    execute_with_world.ptu_full_state = True
    rows = [
        ("exec", "write", _schema({"command": PATH, "timeout_s": {"type": "integer", "minimum": 1, "maximum": 119}}, ["command"]),
         lambda a: ops.execute(configured_root(), chain, limits, a["command"], a.get("timeout_s"), sockets),
         lambda a: "ws:exec"),
        ("read_file", "read", _schema({"path": PATH, "offset": {"type": "integer", "minimum": 0},
                                      "limit": {"type": "integer", "minimum": 1}}, ["path"]),
         lambda a: ops.read_file(configured_root(), a["path"], a.get("offset", 0), a.get("limit", 200)),
         lambda a: "ws:file/" + "/".join(ops._parts(a["path"]))),
        ("write_file", "write", _schema({"path": PATH, "content": STRING}, ["path", "content"]),
         lambda a: ops.write_file(configured_root(), a["path"], a["content"]),
         lambda a: "ws:file/" + "/".join(ops._parts(a["path"]))),
        ("edit_file", "write", _schema({"path": PATH, "old_string": PATH,
                                      "new_string": STRING, "replace_all": {"type": "boolean"}},
                                     ["path", "old_string", "new_string"]),
         lambda a: ops.edit_file(configured_root(), a["path"], a["old_string"], a["new_string"], a.get("replace_all", False)),
         lambda a: "ws:file/" + "/".join(ops._parts(a["path"]))),
    ]
    specs = []
    for name, effect, schema, handler, ref in rows:
        def safe_handler(arguments, selected=handler):
            try:
                return selected(arguments)
            except ValueError as error:
                code = str(error)
                try:
                    from ptu.apps.base import AppError
                except ModuleNotFoundError:
                    raise ValueError(code) from None
                raise AppError(code=code, message=(code + ": use a path relative to work/" if code == "E_WS_NOT_FOUND" else code)) from None

        fields = {"surface": "workstation", "ui_path": "/workstation", "dialog": False}
        fields = {key: value for key, value in fields.items() if key in ToolSpec.__dataclass_fields__}
        selected_handler = (execute_with_world if name == "exec" else
                            lambda a, s, c, i, handler=safe_handler: handler(a))
        specs.append(ToolSpec(name="ws." + name, effect=effect, privilege=False,
                              description=DESCRIPTIONS[name],
                              idempotent=name == "read_file", annotations={"readOnlyHint": effect == "read",
                              "destructiveHint": False, "idempotentHint": name == "read_file",
                              "openWorldHint": False}, input_schema=schema, auth_args=(),
                              resources_fn=lambda a, s, ref=ref, access=effect: [ResourceUse(ref(a), "read" if access == "read" else "write")],
                              handler=selected_handler, **fields))
    return specs


def _allow(actor, tool, args, state):
    return None


def _ref(path):
    return "ws:file/" + "/".join(path[1:]) if len(path) > 1 else "ws:exec"


def _ancestors(ref, state=None):
    return ["ws:exec"] if ref.startswith("ws:") else []


def _no_ref(tool, args, result):
    return None


def _make_app():
    from ptu.apps.base import AppSpec
    return AppSpec(id="ws", reference_product="workstation", fidelity_version="0.3",
                   surfaces=("workstation",), report_unit=lambda ref: ref,
                   tool_specs=_specs, authorize=_allow, refs_for_path=_ref,
                   ancestors=_ancestors, bookkeeping=lambda path, effect: False,
                   created_ref=_no_ref, external_check=None, event_ops={}, unread=None)


# The X1 kernel owns AppSpec. Importing this module before X1 lands is valid for
# direct file-tool tests; app discovery after X1 supplies the concrete instance.
try:
    APP = _make_app()
except ModuleNotFoundError as error:
    if error.name != "ptu.apps.base":
        raise
    APP = None
