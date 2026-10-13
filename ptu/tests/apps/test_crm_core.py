"""HubSpot-equivalent CRM behaviours through the gateway."""

from copy import deepcopy

from ptu.apps.base import Actor
from ptu.apps.crm.app import APP
from ptu.apps.crm.fixtures import build
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import Policy, Scope
from ptu.common.registry import Registry
from ptu.common.state import StateStore


def gateway(fixture="base", role="super_admin", *, acting_for=None):
    docs = build(fixture)
    docs["control"] = {"confirmations": [], "escalations": [], "finish": None}
    actor = Actor("agent:crm", acting_for, role)
    return Gateway(Registry(APP.tool_specs(actor, docs)), StateStore(docs), AuditLog(None),
                   Policy(actor.principal, ["crm.*"], [Scope("crm:**", "write")]),
                   "observe", LogicalClock(), IdFactory(11), actor.principal,
                   apps={"crm": APP}, actors={"crm": actor})


def call(g, name, **args):
    return g.call("crm." + name, args)


def test_search_tickets_exposes_total_and_paging_for_over_100_matches():
    g = gateway()
    template = g.state.docs["crm"]["tickets"]["ticket_one"]
    g.state.docs["crm"]["tickets"] = {
        f"T-{index:04d}": {**deepcopy(template), "subject": "Synthetic billing request"}
        for index in range(136)}
    first = call(g, "search_tickets", status="new", limit=100)["result"]
    assert len(first["tickets"]) == 100
    assert first["total"] == 136 and first["has_more"] is True
    assert first["paging"]["next"]["after"] == "100"
    second = call(g, "search_tickets", status="new", limit=100, after="100")["result"]
    assert [row["id"] for row in second["tickets"]] == [f"T-{index:04d}" for index in range(100, 136)]
    assert second["total"] == 136 and second["has_more"] is False
    assert "paging" not in second
    assert call(g, "search_tickets", after="bad")["code"] == "invalid_cursor"


def test_owner_and_user_rosters_are_directory_reads():
    g = gateway()
    assert call(g, "list_owners")["ok"]
    assert call(g, "list_users")["ok"]
    assert [entry["resources"] for entry in g.log.entries] == [[], []]


def test_default_pipelines_and_stage_guard():
    g = gateway(role="sales_rep")
    pipeline = call(g, "get_pipeline", pipeline="sales")["result"]
    assert len(pipeline["stages"]) == 7
    assert [row["probability"] for row in pipeline["stages"]] == [20, 40, 60, 80, 90, 100, 0]
    ticket = call(g, "get_pipeline", pipeline="support")["result"]
    assert [row["id"] for row in ticket["stages"]] == ["new", "waiting_on_contact", "waiting_on_us", "closed"]
    assert call(g, "update_deal", deal="deal_rep", properties={"stage": "presentation_scheduled"})["code"] == "stage_skip_blocked"
    assert call(g, "update_deal", deal="deal_rep", properties={"stage": "qualified_to_buy"})["code"] == "required_property_missing"
    assert call(g, "update_deal", deal="deal_rep", properties={"stage": "qualified_to_buy", "qualification": "reviewed"})["ok"]
    assert call(g, "update_deal", deal="deal_rep", properties={"stage": "appointment_scheduled"})["code"] == "stage_backward_blocked"
    assert call(g, "update_deal", deal="deal_rep", properties={"stage": "contract_sent"}, bulk=True)["code"] == "bulk_stage_edit_blocked"


def test_property_history_is_independent_of_json_object_key_order():
    first, second = gateway(), gateway()
    left = {"amount": 1200, "forecast_category": "pipeline"}
    right = {"forecast_category": "pipeline", "amount": 1200}
    assert call(first, "update_deal", deal="deal_rep", properties=left)["ok"]
    assert call(second, "update_deal", deal="deal_rep", properties=right)["ok"]
    assert first.state.digest() == second.state.digest()


def test_closed_stage_date_probability_and_admin_override():
    g = gateway()
    result = call(g, "update_deal", deal="deal_rep", properties={"stage": "closed_won"})
    assert result["ok"]
    deal = result["result"]
    assert deal["probability"] == 100
    assert deal["close_date"] == "2026-10-01"
    rep = gateway(role="sales_rep")
    assert call(rep, "update_deal", deal="deal_closed", properties={"amount": 1200})["code"] == "closed_stage_locked"
    assert call(g, "update_deal", deal="deal_closed", properties={"amount": 1200})["ok"]


def test_owner_team_activity_and_history():
    g = gateway()
    assert call(g, "update_deal", deal="deal_rep", properties={"owner": "u_other"})["result"]["team"] == "east"
    note = call(g, "create_note", record="crm:deal/deal_rep", body="Synthetic follow up")
    assert note["ok"]
    deal = call(g, "get_deal", deal="deal_rep")["result"]
    assert deal["last_activity_date"] == "2026-10-01"
    history = call(g, "list_property_history", record="crm:deal/deal_rep", property="owner")["result"]["history"]
    assert history[-1]["value"] == "u_other"
    assert {"date", "source"} <= history[-1].keys()


def test_ticket_record_aliases_use_canonical_labels_for_notes_and_history():
    g = gateway()
    g.state.docs["crm"]["tickets"]["T-2202"] = dict(g.state.docs["crm"]["tickets"]["ticket_one"])
    for record in ("T-2202", "ticket:T-2202", "crm:ticket/T-2202"):
        note = call(g, "create_note", record=record, body="Synthetic follow up")
        assert note["ok"]
        assert g.log.entries[-1]["resources"] == [{"ref": "crm:ticket/T-2202", "access": "write"}]
        assert g.log.entries[-1]["created_ref"] == "crm:ticket/T-2202"
        activities = call(g, "list_activities", record=record)
        assert activities["ok"] and activities["result"]["activities"]
        assert g.log.entries[-1]["resources"] == [{"ref": "crm:ticket/T-2202", "access": "read"}]
        history = call(g, "list_property_history", record=record)
        assert history["ok"] and "history" in history["result"]
        assert g.log.entries[-1]["resources"] == [{"ref": "crm:ticket/T-2202", "access": "read"}]


def test_only_crm_permission_and_role_tools_are_privileged():
    specs = {spec.name: spec for spec in APP.tool_specs(Actor("agent:crm", "u_admin", "super_admin"), {})}
    assert specs["crm.create_note"].privilege is False
    assert specs["crm.update_ticket"].privilege is False
    assert specs["crm.create_user"].privilege is True
    assert specs["crm.deactivate_user"].privilege is True
    assert specs["crm.update_user_role"].privilege is True


def test_user_and_owner_lists_expose_assignable_users():
    g = gateway(role="sales_rep")
    users = call(g, "list_users")["result"]["users"]
    owners = call(g, "list_owners")["result"]["owners"]
    expected = {"u_admin", "u_mgr", "u_rep", "u_other", "u_service", "u_view"}
    assert {row["id"] for row in users} == expected
    assert {row["id"] for row in owners} == expected


def test_super_admin_can_deactivate_and_update_users_with_last_admin_guard():
    g = gateway()
    g.state.docs["crm"]["users"]["u_backup"] = {
        "name": "Synthetic Backup Admin", "role": "super_admin", "team": "west",
        "sales_seat": True, "personal_email_connected": True}
    updated = call(g, "update_user_role", user="u_mgr", role="view_only")
    assert updated["ok"] and updated["result"]["role"] == "view_only"
    deactivated = call(g, "deactivate_user", user="u_backup")
    assert deactivated["ok"] and deactivated["result"]["active"] is False
    assert deactivated["result"]["deactivated_at"] == "2026-10-01"
    assert "u_backup" not in {row["id"] for row in call(g, "list_owners")["result"]["owners"]}
    assert "u_backup" in {row["id"] for row in call(g, "list_users")["result"]["users"]}
    assert call(g, "update_deal", deal="deal_rep",
                properties={"owner": "u_backup"})["code"] == "inactive_owner"
    assert call(g, "create_ticket", properties={"subject": "Synthetic request",
                                                "owner": "u_backup"})["code"] == "inactive_owner"
    assert call(g, "update_user_role", user="u_admin", role="sales_manager")["code"] == "last_super_admin"
    assert call(g, "deactivate_user", user="u_admin")["code"] == "last_super_admin"

    denied = gateway(role="sales_manager")
    assert call(denied, "deactivate_user", user="u_rep")["code"] == "permission_denied"


def test_company_merge_dialog_and_irreversible_result():
    g = gateway("merge")
    preview = call(g, "merge_companies", primary="co_primary", secondary="co_secondary")
    assert preview["result"]["requires_confirmation"] is True
    assert preview["result"]["irreversible"] is True
    assert "co_secondary" in g.state.docs["crm"]["companies"]
    merged = call(g, "merge_companies", primary="co_primary", secondary="co_secondary", confirm=True)
    assert merged["ok"]
    company = merged["result"]
    assert company["name"] == "Primary Synthetic"
    assert company["lifecycle_stage"] == "customer"
    assert set(company["contacts"]) == {"contact_rep", "contact_other"}
    assert "co_secondary" not in g.state.docs["crm"]["companies"]
    assert len(g.state.docs["crm"]["merges"]) == 1


def test_record_scopes_views_export_and_restore_window():
    rep = gateway(role="sales_rep")
    assert call(rep, "get_deal", deal="deal_rep")["ok"]
    assert call(rep, "get_deal", deal="deal_other")["code"] == "permission_denied"
    manager = gateway(role="sales_manager")
    assert call(manager, "get_deal", deal="deal_rep")["ok"]
    assert call(manager, "get_deal", deal="deal_other")["code"] == "permission_denied"
    assert call(rep, "get_view", view="private_manager")["code"] == "permission_denied"
    assert call(rep, "get_view", view="all_deals")["ok"]
    assert call(rep, "export_view", view="all_deals")["code"] == "export_permission_denied"
    admin = gateway()
    preview = call(admin, "export_view", view="all_deals")
    assert preview["result"]["requires_confirmation"] is True
    assert call(admin, "export_view", view="all_deals", confirm=True)["ok"]
    assert len(admin.state.docs["crm"]["exports"]) == 1
    recent = gateway("deleted_recent")
    assert call(recent, "restore_record", record="crm:contact/contact_deleted")["ok"]
    old = gateway("deleted_old")
    assert call(old, "restore_record", record="crm:contact/contact_deleted")["code"] == "restore_window_expired"


def test_lists_sequences_and_enrolment_guard():
    rep = gateway("sequence", role="sales_rep")
    assert call(rep, "get_list", list="list_active")["result"]["kind"] == "active"
    assert call(rep, "get_list", list="list_static")["result"]["kind"] == "static"
    enrolled = call(rep, "enroll_contact", sequence="seq_intro", contact="contact_rep")
    assert enrolled["ok"] and enrolled["result"]["status"] == "active"
    assert call(rep, "enroll_contact", sequence="seq_intro", contact="contact_rep")["code"] == "already_enrolled"
    assert call(rep, "unenroll_contact", sequence="seq_intro", contact="contact_rep")["ok"]
    viewer = gateway("sequence", role="view_only")
    assert call(viewer, "enroll_contact", sequence="seq_intro", contact="contact_rep")["code"] == "permission_denied"
    disconnected = gateway("sequence_disconnected", role="sales_rep")
    assert call(disconnected, "enroll_contact", sequence="seq_intro", contact="contact_rep")["code"] == "personal_email_required"


def test_declared_surfaces_and_absent_workflows():
    specs = APP.tool_specs(Actor("agent:test", None, "super_admin"), {})
    names = {spec.name for spec in specs}
    assert "crm.enroll_contact" in names
    assert "crm.run_workflow" not in names
    assert all(spec.surface in APP.surfaces for spec in specs)


def test_created_note_reports_its_record_and_sequence_reply_stops_future_steps():
    g = gateway("sequence", role="sales_rep")
    note = call(g, "create_note", record="crm:deal/deal_rep", body="Synthetic status")
    assert note["ok"]
    assert APP.created_ref("crm.create_note", {"record": "crm:deal/deal_rep"}, note["result"]) == "crm:deal/deal_rep"
    assert APP.report_unit("crm:deal/deal_rep") == "crm:deal/deal_rep"
    assert call(g, "enroll_contact", sequence="seq_intro", contact="contact_rep")["ok"]
    assert g.state.docs["crm"]["enrollments"][0]["status"] == "active"
    APP.event_ops["sequence_reply"]({"contact": "contact_rep"}, g.state.docs["crm"], LogicalClock(), IdFactory(1))
    enrollment = call(g, "list_enrollments", sequence="seq_intro", contact="contact_rep")["result"]["enrollments"][0]
    assert enrollment["status"] == "unenrolled" and enrollment["reason"] == "reply"


def test_create_deal_validates_properties_and_forecast_category():
    g = gateway()
    assert call(g, "create_deal", properties={"name": "New", "unknown": "value"})["code"] == "invalid_property"
    assert call(g, "create_deal", properties={"name": "New", "forecast_category": "unsupported"})["code"] == "invalid_forecast_category"


def test_sequence_enrolment_is_exclusive_and_respects_contact_delivery():
    g = gateway("enrolled", role="sales_rep")
    g.state.docs["crm"]["sequences"]["seq_followup"] = {
        "id": "seq_followup", "name": "Follow up", "owner": "u_rep", "shared_users": [],
        "steps": [{"kind": "email", "delay_days": 0}]}
    assert call(g, "enroll_contact", sequence="seq_followup", contact="contact_rep")["code"] == "already_enrolled"
    g = gateway("sequence", role="sales_rep")
    g.state.docs["crm"]["contacts"]["contact_rep"]["email"] = ""
    assert call(g, "enroll_contact", sequence="seq_intro", contact="contact_rep")["code"] == "contact_email_required"
    g = gateway("sequence", role="sales_manager")
    g.state.docs["crm"]["sequences"]["seq_intro"]["shared_users"] = []
    assert call(g, "enroll_contact", sequence="seq_intro", contact="contact_rep")["code"] == "sequence_not_shared"


def test_manual_probability_and_default_close_date_follow_documented_rules():
    g = gateway()
    new = call(g, "create_deal", properties={"name": "New synthetic deal"})
    assert new["result"]["close_date"] == "2026-10-31"
    changed = call(g, "update_deal", deal="deal_rep", properties={"probability": 35})
    assert changed["ok"]
    open_stage = call(g, "update_deal", deal="deal_rep", properties={"stage": "qualified_to_buy", "qualification": "reviewed"})
    assert open_stage["result"]["probability"] == 35
    closed = call(g, "update_deal", deal="deal_rep", properties={"stage": "closed_won"})
    assert closed["result"]["probability"] == 100


def test_internal_ledgers_do_not_look_like_record_edits():
    for key in ("next_ids", "activities", "property_history", "merges", "exports", "enrollments", "deleted"):
        assert APP.bookkeeping([], key)
    assert not APP.bookkeeping([], "deals")
    assert APP.ancestors("crm:note/note_1", {}) == []


def test_configured_deal_creation_stage_is_applied():
    g = gateway()
    g.state.docs["crm"]["pipelines"]["sales"]["rules"]["default_create_stage"] = "qualified_to_buy"
    created = call(g, "create_deal", properties={"name": "New synthetic", "qualification": "reviewed"})
    assert created["result"]["stage"] == "qualified_to_buy"
    assert created["result"]["probability"] == 40
