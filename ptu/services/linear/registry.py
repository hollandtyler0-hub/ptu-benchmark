"""Registry metadata for the minimum synthetic Linear tool surface."""

from ptu.common.registry import ResourceUse, ToolSpec

from . import ops


refs_for_path = ops.refs_for_path


def _schema(properties: dict, required: list[str], **extra) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False, **extra}


STRING = {"type": "string", "minLength": 1}
IDENTIFIER = {"identifier": STRING}


def _teams(arguments, state):
    return [ResourceUse(f"linear:team/{key}", "read") for key in sorted(state.get("teams", {}))]


def _team(arguments, state):
    team = arguments["team"]
    return [ResourceUse(f"linear:team/{team}", "read"), *[
        ResourceUse(f"linear:project/{team}/{slug}", "read")
        for slug in sorted(state["teams"][team].get("projects", {}))]]


def _issues(arguments, state):
    project_slug = None
    if arguments.get("project"):
        project_path = arguments["project"]
        if project_path.count("/") == 1:
            team, project_slug = project_path.split("/", 1)
        elif arguments.get("team") and "/" not in project_path:
            team, project_slug = arguments["team"], project_path
        else:
            raise ValueError("E_BAD_PROJECT")
        if arguments.get("team") and arguments["team"] != team:
            return []
        parent = f"linear:project/{team}/{project_slug}"
    elif arguments.get("team"):
        team = arguments["team"]
        parent = f"linear:team/{team}"
    else:
        raise ValueError("E_SCOPE_REQUIRED")
    page = 1 if arguments.get("page") is None else arguments["page"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("E_BAD_PAGE")
    rows = [
        identifier for identifier, row in sorted(state.get("issues", {}).items())
        if row.get("team") == team and (project_slug is None or row.get("project") == project_slug)]
    return [ResourceUse(parent, "read"), *[
        ResourceUse(f"linear:issue/{identifier}", "read")
        for identifier in rows[(page - 1) * 30:page * 30]]]


def _issue(access: str):
    def resource(arguments, state):
        return [ResourceUse(f"linear:issue/{arguments['identifier']}", access)]
    return resource


def _new_issue(arguments, state):
    team_key = arguments["team"]
    team = state["teams"][team_key]
    number = team.get("next_issue_number")
    if number is None:
        number = max((int(key.split("-", 1)[1]) for key in state.get("issues", {})
                      if key.startswith(team_key + "-") and key.split("-", 1)[1].isdigit()), default=0) + 1
    project = ops.project_slug(team_key, arguments.get("project"))
    if state.get("world_scale") != "large":
        uses = [ResourceUse(f"linear:issue/{team_key}-{number}", "write")]
        if project:
            uses.append(ResourceUse(f"linear:project/{team_key}/{project}", "read"))
        return uses
    if project:
        return [ResourceUse(f"linear:project/{team_key}/{project}", "write"),
                ResourceUse(f"linear:team/{team_key}", "read")]
    return [ResourceUse(f"linear:team/{team_key}", "write")]


def tool_specs(principal: str = "agent:release-bot") -> list[ToolSpec]:
    rows = [
        ("list_teams", "read", _schema({}, []), _teams),
        ("list_projects", "read", _schema({"team": STRING}, ["team"]), _team),
        ("list_issues", "read", _schema({"team": STRING, "project": STRING,
                                          "page": {"type": "integer", "minimum": 1,
                                                   "description": "One-based page number; 30 issues per page."}}, [],
                                         anyOf=[{"required": ["team"]}, {"required": ["project"]}]), _issues),
        ("get_issue", "read", _schema(IDENTIFIER, ["identifier"]), _issue("read")),
        ("list_comments", "read", _schema(IDENTIFIER, ["identifier"]), _issue("read")),
        ("create_issue", "write", _schema({"team": STRING, "project": STRING, "title": STRING,
                                            "description": {"type": "string"}}, ["team", "title"]), _new_issue),
        ("update_issue", "write", _schema({**IDENTIFIER, "state": {"type": "string"},
                                            "assignee": {"type": ["string", "null"]},
                                            "priority": {"type": "integer"},
                                            "labels": {"type": "array", "items": {"type": "string"}}},
                                           ["identifier"], anyOf=[{"required": [field]} for field in ("state", "assignee", "priority", "labels")]), _issue("write")),
        ("comment_on_issue", "write", _schema({**IDENTIFIER, "body": {"type": "string"}},
                                                 ["identifier", "body"]), _issue("write")),
        ("delete_issue", "destructive", _schema(IDENTIFIER, ["identifier"]), _issue("write")),
    ]
    specs = []
    for name, effect, input_schema, resources_fn in rows:
        operation = getattr(ops, name)

        def handler(arguments, state, clock, ids, operation=operation):
            return operation(arguments, state, clock, ids, principal)

        idempotent = effect == "read"
        specs.append(ToolSpec(name="linear." + name, effect=effect, privilege=False,
                              idempotent=idempotent,
                              annotations={"readOnlyHint": effect == "read", "destructiveHint": effect == "destructive",
                                           "idempotentHint": idempotent, "openWorldHint": False},
                              input_schema=input_schema, auth_args=(),
                              resources_fn=resources_fn, handler=handler,
                              description="List team or project issues in pages of 30. Use project=TEAM/slug or team=TEAM with project=slug."
                              if name == "list_issues" else ""))
    return specs
