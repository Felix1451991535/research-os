"""K3 Approval & Policy contract."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class ApprovalSubjectSpec:
    name: str
    description: str
    reauth_when_remote: bool = False   # password re-entry if approved from a non-host device
    ttl_hours: int = 24


class Autonomy(StrEnum):
    AUTO = "auto"      # agent may do without asking
    ASK = "ask"        # requires an Approval
    NEVER = "never"    # no one but the user via settings UI; agents never


# ARCHITECTURE §8. Keys are action classes referenced by services when they gate an action.
AUTONOMY: dict[str, Autonomy] = {
    "read.library": Autonomy.AUTO,
    "read.allowlisted_root": Autonomy.AUTO,
    "read.public_api": Autonomy.AUTO,
    "egress.private_to_cloud_model": Autonomy.ASK,
    "read.secrets": Autonomy.NEVER,
    "compute.local": Autonomy.AUTO,
    "exec.unreviewed_code": Autonomy.NEVER,
    "write.data_dir": Autonomy.AUTO,
    "spend.within_budget": Autonomy.AUTO,
    "spend.over_task_budget": Autonomy.ASK,
    "spend.premium_switch_off": Autonomy.ASK,
    "budget.change": Autonomy.NEVER,
    "experiment.narrow_space": Autonomy.AUTO,
    "experiment.queue_within_approval": Autonomy.AUTO,
    "experiment.widen_or_change": Autonomy.ASK,
    "experiment.final_eval": Autonomy.ASK,
    "experiment.read_test_before_freeze": Autonomy.NEVER,
    "files.dry_run": Autonomy.AUTO,
    "files.apply": Autonomy.ASK,
    "files.permanent_delete": Autonomy.NEVER,
    "identity.propose_merge": Autonomy.AUTO,
    "identity.confirm_merge": Autonomy.ASK,
    "trust.set_human_checked": Autonomy.NEVER,  # user action only
    "external.publish": Autonomy.ASK,
    "policy.change": Autonomy.NEVER,
    "system.install_plugin": Autonomy.ASK,
    "system.enable_connector": Autonomy.ASK,
}
